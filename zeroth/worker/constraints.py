"""Platform rules that can be checked without deploying.

Zerops documents a set of non-negotiable constraints, and violating any of them
produces a deployment that fails minutes later with an error that does not name
the cause. Every rule here is one that can be decided by reading the YAML, so
checking it costs nothing and turns a class-3 failure (a full build cycle) into
a class-1 failure (local, free) - which is the whole argument the project makes
about classifying failures.

These are checks against published platform rules, not taste. Anything
subjective belongs in the deployability report, not here.
"""
import re

import yaml


class ConstraintViolation(Exception):
    pass


# (id, applies-to, message) - kept flat and readable rather than clever.
LOCALHOST_RE = re.compile(r"\b(127\.0\.0\.1|localhost)\b")


def check(zerops_yaml: str) -> list[str]:
    """Return a list of violations. Empty means nothing detectable is wrong."""
    try:
        doc = yaml.safe_load(zerops_yaml) or {}
    except yaml.YAMLError as exc:
        return [f"zerops.yaml is not valid YAML: {exc}"]

    problems: list[str] = []
    for setup in doc.get("zerops") or []:
        name = setup.get("setup", "<unnamed>")
        build = setup.get("build") or {}
        run = setup.get("run") or {}

        _check_binding(problems, name, run)
        _check_prepare_paths(problems, name, run)
        _check_deploy_files(problems, name, build)
        _check_os_match(problems, name, build, run)
        _check_ports(problems, name, run)
        _check_next_standalone(problems, name, run)
    return problems


def _check_binding(problems, name, run) -> None:
    """The L7 balancer routes to the container IP; localhost is unreachable."""
    start = str(run.get("start") or "")
    if LOCALHOST_RE.search(start):
        problems.append(
            f"{name}: the start command binds localhost. Zerops routes to the container "
            f"address, so a process on localhost is unreachable and returns 502. Bind 0.0.0.0."
        )


def _check_prepare_paths(problems, name, run) -> None:
    """prepareCommands run before deploy files arrive."""
    for cmd in run.get("prepareCommands") or []:
        if "/var/www" in str(cmd):
            problems.append(
                f"{name}: prepareCommands references /var/www, which is still empty at that "
                f"point - deploy files have not arrived. Use addToRunPrepare instead."
            )


def _check_deploy_files(problems, name, build) -> None:
    """Without deployFiles the run container starts empty."""
    if not build.get("deployFiles"):
        problems.append(
            f"{name}: no deployFiles. Build and run are separate containers and deployFiles "
            f"is the only bridge, so the run container would start empty."
        )


def _check_os_match(problems, name, build, run) -> None:
    """Wheels and binaries built against one libc do not run on the other."""
    b, r = build.get("os"), run.get("os")
    if b and r and b != r:
        problems.append(
            f"{name}: build.os is {b} but run.os is {r}. Dependencies compiled against one "
            f"libc will not load on the other; keep them the same."
        )


def _check_ports(problems, name, run) -> None:
    for port in run.get("ports") or []:
        number = port.get("port")
        if not isinstance(number, int) or not (1 <= number <= 65535):
            problems.append(f"{name}: port {number!r} is not a valid port number.")


def _check_next_standalone(problems, name, run) -> None:
    """Next.js standalone needs HOSTNAME set and its assets moved into place."""
    start = str(run.get("start") or "")
    if ".next/standalone" not in start:
        return
    if "HOSTNAME=0.0.0.0" not in start:
        problems.append(
            f"{name}: a Next.js standalone server binds localhost unless HOSTNAME is set, and "
            f"HOSTNAME is reserved so it cannot come from envVariables. Start it with "
            f"`env HOSTNAME=0.0.0.0 ...`."
        )
    init = " ".join(str(c) for c in (run.get("initCommands") or []))
    if ".next/static" not in init:
        problems.append(
            f"{name}: standalone tracing does not copy .next/static or public/ next to "
            f"server.js, so static assets 404. Copy them in initCommands."
        )
