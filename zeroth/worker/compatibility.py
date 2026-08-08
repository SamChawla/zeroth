"""Can this repository be deployed to Zerops as it stands?

This answers the question a user actually asks first - "will this work?" -
before any configuration is written, and it answers it deterministically. Every
finding cites the file that produced it, for the same reason fingerprinting
does: a verdict you cannot trace is a verdict you cannot act on.

Deliberately NOT a model call. The checks below are the ones that can be
decided from facts, and deciding them from facts means this stage costs
nothing, cannot hallucinate a blocker, and is unit-testable without an API key.
Anything genuinely ambiguous is left to the analyze stage rather than guessed
at here.
"""
from dataclasses import asdict, dataclass, field

from zeroth.worker.manifest_schema import ALLOWED_TYPES

# Languages Zerops has a runtime for, mapped to the base each one deploys onto.
# Versions are resolved against ALLOWED_TYPES so this cannot drift from what
# the generator is permitted to emit.
SUPPORTED_LANGUAGES = {
    "python": "python",
    "javascript": "nodejs",
    "typescript": "nodejs",
    "node": "nodejs",
    "nodejs": "nodejs",
    "go": "go",
    "golang": "go",
    "php": "php-apache",
    "static": "static",
}

# A dependency manifest is what makes a build reproducible; without one there
# is nothing to install and the runtime has to be guessed.
DEPENDENCY_MANIFESTS = {
    "python": ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"),
    "nodejs": ("package.json",),
    "go": ("go.mod",),
    "php-apache": ("composer.json",),
}

VERDICTS = ("deployable", "needs_changes", "unsupported")


@dataclass
class Finding:
    level: str  # blocker | change | note
    title: str
    detail: str
    evidence: str = ""


@dataclass
class Compatibility:
    verdict: str
    headline: str
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "headline": self.headline,
            "findings": [asdict(f) for f in self.findings],
        }


def assess(fp) -> Compatibility:
    """Judge a fingerprint. fp is a Fingerprint, or its dict form."""
    facts = fp if isinstance(fp, dict) else fp.to_dict()
    findings: list[Finding] = []

    language = (facts.get("language") or "unknown").lower()
    base = SUPPORTED_LANGUAGES.get(language)
    present = set(facts.get("present_files") or [])

    if not base:
        findings.append(Finding(
            "blocker",
            f"No Zerops runtime for {language}",
            "Zerops has runtimes for Python, Node, Go, PHP and static sites. "
            "This repository does not look like any of them, so there is nothing "
            "to deploy it onto.",
            "detected language",
        ))
        return Compatibility("unsupported", f"Not deployable — {language} is not a Zerops runtime", findings)

    _check_runtime_version(findings, facts, base)
    _check_dependency_manifest(findings, base, present)
    _check_entrypoint(findings, facts, base)
    _check_port(findings, facts)
    _check_backing_services(findings, facts)
    _check_containerisation(findings, present)

    return _verdict(findings, base, facts)


def _check_runtime_version(findings, facts, base) -> None:
    version = (facts.get("runtime_version") or "").strip()
    if not version:
        findings.append(Finding(
            "note",
            "No pinned runtime version",
            f"No version was declared, so the newest supported {base} will be used. "
            "Pin one if the application needs a specific version.",
        ))
        return
    if f"{base}@{version}" not in ALLOWED_TYPES:
        available = sorted(t.split("@")[1] for t in ALLOWED_TYPES if t.startswith(f"{base}@"))
        findings.append(Finding(
            "change",
            f"{base} {version} is not available on Zerops",
            f"The deployment will use the closest supported version instead. "
            f"Available: {', '.join(available) or 'none'}.",
            "declared runtime version",
        ))


def _check_dependency_manifest(findings, base, present) -> None:
    expected = DEPENDENCY_MANIFESTS.get(base, ())
    if expected and not (present & set(expected)):
        findings.append(Finding(
            "change",
            "No dependency manifest",
            f"None of {', '.join(expected)} was found, so the build has nothing to "
            "install. Add one, or the application must genuinely have no dependencies.",
        ))


def _check_entrypoint(findings, facts, base) -> None:
    if base == "static":
        return
    if facts.get("entrypoints") or "Procfile" in set(facts.get("present_files") or []):
        return
    findings.append(Finding(
        "change",
        "No start command detected",
        "Nothing in the repository says how to start the application. One will be "
        "inferred from the framework, which is worth checking before you rely on it.",
    ))


def _check_port(findings, facts) -> None:
    if facts.get("ports"):
        return
    findings.append(Finding(
        "note",
        "No port declared",
        "No port was found in the configuration, so a conventional one will be used. "
        "The application must bind 0.0.0.0 rather than localhost to be reachable.",
    ))


def _check_backing_services(findings, facts) -> None:
    """Databases and caches are the usual reason a repo needs changes.

    A repository that talks to Postgres on localhost works on a laptop and
    fails on any platform, because the database is a separate service with its
    own hostname. This is the single most common real-world blocker.
    """
    env_vars = [v.upper() for v in (facts.get("env_vars") or [])]
    for kind, keywords in (("database", ("DATABASE_URL", "POSTGRES", "MYSQL", "DB_HOST")),
                           ("cache", ("REDIS_URL", "VALKEY_URL", "CACHE_URL"))):
        declared = facts.get("databases" if kind == "database" else "caches") or []
        referenced = any(any(k in v for k in keywords) for v in env_vars)
        if declared and not referenced:
            findings.append(Finding(
                "change",
                f"{kind.title()} detected with no connection variable",
                f"A {kind} was found in the dependencies but nothing reads a connection "
                f"string from the environment. Zerops supplies one per service, so the "
                f"application needs to read it rather than hardcode a host.",
                f"declared {kind}: {', '.join(declared)}",
            ))
        elif declared:
            findings.append(Finding(
                "note",
                f"{kind.title()} will be provisioned",
                f"{', '.join(declared)} — wired through an environment variable, which is "
                "what Zerops provides.",
            ))


def _check_containerisation(findings, present) -> None:
    if "Dockerfile" in present:
        findings.append(Finding(
            "note",
            "Dockerfile present",
            "Zerops builds from the runtime base rather than your Dockerfile. The build "
            "and start commands will be derived from the repository instead.",
            "Dockerfile",
        ))
    if "docker-compose.yml" in present or "docker-compose.yaml" in present:
        findings.append(Finding(
            "note",
            "Compose file present",
            "Its services are read as evidence for what to provision, but Zerops runs "
            "them as managed services rather than as containers.",
            "docker-compose.yml",
        ))


def _verdict(findings, base, facts) -> Compatibility:
    if any(f.level == "blocker" for f in findings):
        return Compatibility("unsupported", "Not deployable without changes", findings)

    changes = [f for f in findings if f.level == "change"]
    stack = f"{base} {facts.get('runtime_version') or ''}".strip()
    if changes:
        count = len(changes)
        return Compatibility(
            "needs_changes",
            f"Deployable with {count} change{'' if count == 1 else 's'}",
            findings,
        )
    return Compatibility("deployable", f"Deployable as-is — {stack}", findings)
