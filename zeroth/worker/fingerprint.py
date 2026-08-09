"""Deterministic repository analysis.

The model never reads application source. This module extracts facts from
manifests and config files, each carrying the evidence that produced it, and
only those facts are sent on for reasoning. Every generated service can
therefore answer "why is this here?" with a file and a line.
"""
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

MANIFEST_FILES = (
    "requirements.txt", "pyproject.toml", "Pipfile", "package.json", "go.mod",
    "composer.json", "Gemfile", "pom.xml", "build.gradle",
)
CONFIG_FILES = (
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".env.example",
    ".env.sample", "Procfile", "runtime.txt", ".python-version", ".nvmrc",
)

# dependency substring -> (fact key, value, zerops service type hint)
DEPENDENCY_SIGNALS = {
    "psycopg": ("database", "postgresql"),
    "asyncpg": ("database", "postgresql"),
    "pg8000": ("database", "postgresql"),
    "sqlalchemy": ("orm", "sqlalchemy"),
    "pymysql": ("database", "mariadb"),
    "mysqlclient": ("database", "mariadb"),
    "mysql2": ("database", "mariadb"),
    "pymongo": ("database", "mongodb"),
    "mongoose": ("database", "mongodb"),
    "redis": ("cache", "valkey"),
    "ioredis": ("cache", "valkey"),
    "celery": ("worker", "celery"),
    "rq": ("worker", "rq"),
    "bullmq": ("worker", "bullmq"),
    "django": ("framework", "django"),
    "fastapi": ("framework", "fastapi"),
    "flask": ("framework", "flask"),
    "express": ("framework", "express"),
    "next": ("framework", "nextjs"),
    "gunicorn": ("server", "gunicorn"),
    "uvicorn": ("server", "uvicorn"),
    "pg": ("database", "postgresql"),
}


@dataclass
class Fact:
    key: str
    value: str
    evidence: str


@dataclass
class Fingerprint:
    repo_name: str = ""
    language: str = "unknown"
    runtime_version: str = ""
    framework: str = ""
    databases: list[str] = field(default_factory=list)
    caches: list[str] = field(default_factory=list)
    has_worker: bool = False
    env_vars: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    compose_services: list[str] = field(default_factory=list)
    present_files: list[str] = field(default_factory=list)
    # Set when the application does not live at the repository root but in a
    # single subdirectory (repo/blogWebsite/manage.py and nothing at the top).
    # Deploys use this as the working directory.
    project_subdir: str = ""
    tree: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)

    def add(self, key: str, value: str, evidence: str) -> None:
        if not any(f.key == key and f.value == value for f in self.facts):
            self.facts.append(Fact(key, value, evidence))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["facts"] = [asdict(f) for f in self.facts]
        return data


def _read_smart(path: Path, limit: int = 20_000) -> tuple[str, str]:
    """Read a text file, surviving Windows encodings. Returns (text, encoding).

    `pip freeze > requirements.txt` in PowerShell writes UTF-16, which Linux
    pip cannot parse and which reads as NUL-riddled garbage in UTF-8. Treating
    that as "no dependencies" produced a wrong verdict on a real repository;
    the encoding is itself a finding, so it is returned alongside the text.
    """
    try:
        raw = path.read_bytes()[: limit * 4]
    except OSError:
        return "", "unreadable"
    for bom, enc in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8-sig")):
        if raw.startswith(bom):
            try:
                return raw.decode(enc)[:limit], enc
            except UnicodeDecodeError:
                break
    try:
        return raw.decode("utf-8")[:limit], "utf-8"
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")[:limit], "unknown"


def _read(path: Path, limit: int = 20_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _parse_python_deps(text: str) -> list[str]:
    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[=<>!\[~;\s]", line)[0].strip().lower()
        if name:
            deps.append(name)
    return deps



# Directory names that conventionally hold the application when it is not at
# the root. A tie-breaker, not a filter - anything with a manifest is a
# candidate.
_PREFERRED_ROOTS = ("src", "app", "backend", "server", "api", "web", "service")

_SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "docs", "test", "tests",
              "examples", "example", ".git", "__pycache__"}



# Entry-point names that identify a language even with no manifest present.
_SOURCE_HINTS = {
    "python": ("manage.py", "app.py", "main.py", "wsgi.py", "asgi.py"),
    "nodejs": ("server.js", "index.js", "app.js"),
    "go": ("main.go",),
    "php": ("index.php",),
}


def _detect_language_from_sources(repo_dir: Path, fp: Fingerprint) -> None:
    """Best-effort language from source files, cited like everything else."""
    dirs = [repo_dir] + [d for d in repo_dir.iterdir()
                         if d.is_dir() and not d.name.startswith(".")
                         and d.name.lower() not in _SKIP_DIRS]
    for d in dirs[:8]:
        try:
            names = {f.name for f in d.iterdir() if f.is_file()}
        except OSError:
            continue
        for lang, hints in _SOURCE_HINTS.items():
            hit = sorted(names & set(hints))
            if hit:
                where = "" if d == repo_dir else f"{d.name}/"
                fp.language = lang
                fp.add("language", lang, f"{where}{hit[0]} present (no manifest anywhere)")
                if not fp.entrypoints:
                    fp.entrypoints = [f"{where}{hit[0]}"]
                return

def _find_project_roots(repo_dir: Path) -> list[Path]:
    """Directories (1-2 levels down) containing a dependency manifest, ranked.

    Shallower wins, then a conventional name, then more manifest files. The
    example/test/vendor family is skipped: a manifest in tests/ describes the
    tests, not the product.
    """
    found = []
    for level1 in repo_dir.iterdir():
        if not level1.is_dir() or level1.name.startswith(".") or level1.name.lower() in _SKIP_DIRS:
            continue
        subdirs = [d for d in level1.iterdir()
                   if d.is_dir() and not d.name.startswith(".") and d.name.lower() not in _SKIP_DIRS]
        for depth, candidate in [(1, level1)] + [(2, d) for d in subdirs]:
            try:
                names = {f.name for f in candidate.iterdir() if f.is_file()}
            except OSError:
                continue
            manifests = names & set(MANIFEST_FILES)
            if manifests:
                preferred = 0 if candidate.name.lower() in _PREFERRED_ROOTS else 1
                found.append((depth, preferred, -len(manifests), candidate))
    found.sort(key=lambda t: t[:3])
    return [t[3] for t in found]

def build(repo_dir: Path, repo_name: str) -> Fingerprint:
    fp = Fingerprint(repo_name=repo_name)
    root_files = {p.name for p in repo_dir.iterdir() if p.is_file()}

    # A very common shape: nothing but a README at the root and the whole
    # application somewhere below. Judging the root alone calls such a
    # repository "unknown", which is wrong in the way that matters most - the
    # verdict. Search one and two levels down, rank the candidates, pick the
    # best and record the others as evidence so the choice can be argued with.
    if not (root_files & set(MANIFEST_FILES)):
        candidates = _find_project_roots(repo_dir)
        if candidates:
            chosen = candidates[0]
            rel = str(chosen.relative_to(repo_dir))
            others = ", ".join(str(c.relative_to(repo_dir)) for c in candidates[1:4])
            fp.project_subdir = rel
            fp.add("project_root", rel,
                   f"no manifest at the repository root; {rel}/ has one"
                   + (f" (also considered: {others})" if others else ""))
            repo_dir = chosen
            root_files = {p.name for p in repo_dir.iterdir() if p.is_file()}
        else:
            searched = ", ".join(sorted(
                d.name + "/" for d in repo_dir.iterdir()
                if d.is_dir() and not d.name.startswith("."))[:12]) or "no subdirectories"
            fp.add("manifest_search", "none-found", searched)
            # No manifest anywhere, but the sources still say what this is.
            # Without this, the verdict degrades to "unknown runtime" when the
            # honest, actionable answer is "python app with no requirements
            # file" - a different problem with a different fix.
            _detect_language_from_sources(repo_dir, fp)

    fp.present_files = sorted(
        f for f in root_files if f in MANIFEST_FILES + CONFIG_FILES
    )
    fp.tree = sorted(
        p.name + ("/" if p.is_dir() else "")
        for p in repo_dir.iterdir()
        if not p.name.startswith(".git")
    )[:60]

    _detect_python(repo_dir, root_files, fp)
    _detect_node(repo_dir, root_files, fp)
    _detect_go(repo_dir, root_files, fp)
    _detect_compose(repo_dir, root_files, fp)
    _detect_dockerfile(repo_dir, root_files, fp)
    _detect_env(repo_dir, root_files, fp)
    _detect_procfile(repo_dir, root_files, fp)
    _apply_dependency_signals(fp)
    _detect_library(repo_dir, root_files, fp)

    if not fp.ports:
        fp.ports = [8000 if fp.language == "python" else 3000]
        fp.add("port", str(fp.ports[0]), "default for detected runtime")

    return fp


def _detect_library(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    """Is this a package meant for installation rather than an app meant to run?

    A library repo analyzed as an application produces a fabricated start
    command and a doomed deploy. The tells are packaging machinery at the root
    with nothing runnable next to it: pyproject/setup plus MANIFEST.in or
    publishing tools, and no manage.py, wsgi/asgi module, Procfile or main
    entrypoint. Cited like every other fact so the verdict can be argued with.
    """
    packaging = root_files & {"pyproject.toml", "setup.py", "setup.cfg"}
    if not packaging:
        return
    publish_markers = ("twine" in fp.dependencies or "build" in fp.dependencies
                       or "MANIFEST.in" in root_files)
    if not publish_markers:
        return
    runnable = (
        "manage.py" in root_files or "Procfile" in root_files
        or fp.entrypoints
        or any((repo_dir / n).is_file() for n in ("wsgi.py", "asgi.py", "app.py", "main.py"))
    )
    if runnable:
        return

    evidence = f"{', '.join(sorted(packaging | (root_files & {'MANIFEST.in'})))}; no manage.py, wsgi/asgi, Procfile or main module at the project root"
    demo = next((d.name for d in repo_dir.iterdir()
                 if d.is_dir() and "example" in d.name.lower() and (d / "manage.py").is_file()), "")
    if demo:
        evidence += f"; a runnable demo exists in {demo}/"
    fp.add("packaging", "library", evidence)


def _detect_python(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    if "requirements.txt" in root_files:
        text, encoding = _read_smart(repo_dir / "requirements.txt")
        deps = _parse_python_deps(text)
        fp.dependencies.extend(deps)
        fp.language = "python"
        fp.add("language", "python", "requirements.txt present")
        if encoding.startswith("utf-16"):
            # Real finding, not trivia: Linux pip cannot parse UTF-16, so the
            # build fails on this file exactly as it is committed.
            fp.add("requirements_encoding", encoding,
                   "requirements.txt is UTF-16 (Windows pip freeze); Linux pip cannot read it")
    if "pyproject.toml" in root_files:
        fp.language = "python"
        fp.add("language", "python", "pyproject.toml present")
        text = _read(repo_dir / "pyproject.toml")
        fp.dependencies.extend(
            m.lower() for m in re.findall(r'"([A-Za-z0-9_.-]+)\s*[><=~]', text)
        )
        req = re.search(r'requires-python\s*=\s*"[^0-9]*([0-9.]+)', text)
        if req:
            fp.runtime_version = req.group(1)
            fp.add("runtime_version", req.group(1), "requires-python in pyproject.toml")

    if (repo_dir / "manage.py").exists():
        fp.framework = "django"
        fp.entrypoints.append("manage.py")
        fp.add("framework", "django", "manage.py at repository root")

    for candidate in ("runtime.txt", ".python-version"):
        if candidate in root_files:
            version = re.search(r"([0-9]+\.[0-9]+)", _read(repo_dir / candidate))
            if version:
                fp.runtime_version = version.group(1)
                fp.add("runtime_version", version.group(1), f"{candidate}")


def _detect_node(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    if "package.json" not in root_files:
        return
    try:
        pkg = json.loads(_read(repo_dir / "package.json"))
    except json.JSONDecodeError:
        return
    if fp.language == "unknown":
        fp.language = "nodejs"
        fp.add("language", "nodejs", "package.json present")
    fp.dependencies.extend(k.lower() for k in (pkg.get("dependencies") or {}))
    scripts = pkg.get("scripts") or {}
    if "start" in scripts:
        fp.entrypoints.append(f"npm start -> {scripts['start']}")
        fp.add("start_command", scripts["start"], "scripts.start in package.json")


def _detect_go(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    if "go.mod" not in root_files:
        return
    if fp.language == "unknown":
        fp.language = "go"
        fp.add("language", "go", "go.mod present")
    version = re.search(r"^go\s+([0-9.]+)", _read(repo_dir / "go.mod"), re.M)
    if version:
        fp.runtime_version = version.group(1)
        fp.add("runtime_version", version.group(1), "go directive in go.mod")


def _detect_compose(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    name = next((n for n in ("docker-compose.yml", "docker-compose.yaml") if n in root_files), None)
    if not name:
        return
    try:
        data = yaml.safe_load(_read(repo_dir / name)) or {}
    except yaml.YAMLError:
        return
    for svc_name, svc in (data.get("services") or {}).items():
        fp.compose_services.append(svc_name)
        image = str((svc or {}).get("image", "")).lower()
        if "postgres" in image:
            fp.databases.append("postgresql")
            fp.add("database", "postgresql", f"{name}: service '{svc_name}' uses {image}")
        if "redis" in image or "valkey" in image:
            fp.caches.append("valkey")
            fp.add("cache", "valkey", f"{name}: service '{svc_name}' uses {image}")
        if "mysql" in image or "maria" in image:
            fp.databases.append("mariadb")
            fp.add("database", "mariadb", f"{name}: service '{svc_name}' uses {image}")
        if "mongo" in image:
            fp.databases.append("mongodb")
            fp.add("database", "mongodb", f"{name}: service '{svc_name}' uses {image}")
        for port in (svc or {}).get("ports") or []:
            match = re.search(r"(\d+)\s*$", str(port))
            if match:
                fp.ports.append(int(match.group(1)))


def _detect_dockerfile(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    if "Dockerfile" not in root_files:
        return
    text = _read(repo_dir / "Dockerfile")
    base = re.search(r"^FROM\s+(\S+)", text, re.M | re.I)
    if base:
        fp.add("base_image", base.group(1), "FROM line in Dockerfile")
        version = re.search(r"(python|node|golang):([0-9.]+)", base.group(1), re.I)
        if version and not fp.runtime_version:
            fp.runtime_version = version.group(2)
            fp.add("runtime_version", version.group(2), "Dockerfile base image tag")
    for port in re.findall(r"^EXPOSE\s+(\d+)", text, re.M | re.I):
        fp.ports.append(int(port))
        fp.add("port", port, "EXPOSE in Dockerfile")
    cmd = re.search(r"^CMD\s+(.+)$", text, re.M)
    if cmd:
        fp.add("start_command", cmd.group(1).strip(), "CMD in Dockerfile")


def _detect_env(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    name = next((n for n in (".env.example", ".env.sample") if n in root_files), None)
    if not name:
        return
    for line in _read(repo_dir / name).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            fp.env_vars.append(key)
    if fp.env_vars:
        fp.add("env_vars", f"{len(fp.env_vars)} declared", name)
    joined = " ".join(fp.env_vars).upper()
    if "DATABASE_URL" in joined or "POSTGRES" in joined:
        fp.databases.append("postgresql")
        fp.add("database", "postgresql", f"DATABASE_URL referenced in {name}")
    if "REDIS" in joined:
        fp.caches.append("valkey")
        fp.add("cache", "valkey", f"REDIS_* referenced in {name}")


def _detect_procfile(repo_dir: Path, root_files: set[str], fp: Fingerprint) -> None:
    if "Procfile" not in root_files:
        return
    for line in _read(repo_dir / "Procfile").splitlines():
        if ":" not in line:
            continue
        proc, command = line.split(":", 1)
        proc = proc.strip().lower()
        if proc == "web":
            fp.add("start_command", command.strip(), "web process in Procfile")
        if proc in {"worker", "celery"}:
            fp.has_worker = True
            fp.add("worker", command.strip(), f"{proc} process in Procfile")


def _apply_dependency_signals(fp: Fingerprint) -> None:
    manifest = "requirements.txt" if fp.language == "python" else "package.json"
    for dep in set(fp.dependencies):
        for needle, (key, value) in DEPENDENCY_SIGNALS.items():
            if dep == needle or dep.startswith(needle + "-"):
                evidence = f"'{dep}' in {manifest}"
                if key == "database":
                    fp.databases.append(value)
                elif key == "cache":
                    fp.caches.append(value)
                elif key == "worker":
                    fp.has_worker = True
                elif key == "framework" and not fp.framework:
                    fp.framework = value
                fp.add(key, value, evidence)

    fp.databases = sorted(set(fp.databases))
    fp.caches = sorted(set(fp.caches))
    fp.ports = sorted(set(fp.ports))
    fp.dependencies = sorted(set(fp.dependencies))[:80]
    fp.env_vars = sorted(set(fp.env_vars))
