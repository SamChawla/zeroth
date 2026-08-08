"""zCLI-backed provider.

Shelling out to zcli is deliberate: it is the interface Zerops documents and
keeps stable, and it avoids hand-rolling auth and polling against the REST API
under a 48-hour clock.

Flag names below are confirmed against `zcli <cmd> --help` (zcli 1.1.0,
2026-08-08): project/service identifiers are `-P/--project-id` and
`-S/--service-id` — NOT the `--projectId`/`--serviceId` this file guessed
before that check. `project project-import` and `project delete` both take
the id as a bare positional argument too; the flag form is used here for
readability.

STILL SPIKE-DEPENDENT — confirmed command shapes are not the same as
confirmed behavior. Run a real `project-import` before trusting this:
  1. What does `project project-import` print on success, and does the id
     regex below actually match it? (`create_project` — unverified guess.)
  2. `buildFromGit` is a project-import YAML field, which strongly implies
     Zerops clones and builds server-side once the project is created —
     meaning `deploy()` doesn't need a `service push`/`deploy` step, only
     polling. Not yet watched end-to-end.
  3. `service log` needs `-S/--service-id` to mean anything once a project
     has more than one service — ours always will (api/worker/web/db/cache).
     `logs()`/`deploy()` below only look at the whole project; they need a
     hostname→service-id map from `service list` output, which is still
     unread.
  4. Does `service list` output actually contain the literal strings
     "failed"/"error"/"active"/"running" this file greps for?
  5. Where does the live subdomain URL surface — `service list`, `project
     env`, something else?
  6. Does `project delete --confirm` free quota immediately?
"""
import re
import subprocess
import tempfile
import time
from pathlib import Path

from zeroth.config import settings
from zeroth.worker.providers.base import DeployResult


class ZcliError(Exception):
    pass


class ZcliProvider:
    """
    Confirmed 2026-08-08: zcli has no ZEROPS_TOKEN env-var auth. `zcli login
    <token>` makes an authenticated call and persists the session to a local
    config file (cli.data) — that's the only way in. The old `_run()` passed
    ZEROPS_TOKEN as a subprocess env var, which zcli never read, AND replaced
    the entire environment (dropping HOME/PATH/etc, breaking zcli's ability to
    find its own config dir). Fixed: `_ensure_login()` runs the real login
    command once per process, and `_run()` no longer touches the environment.
    """

    name = "zcli"

    def __init__(self) -> None:
        self._logged_in = False

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        proc = subprocess.run(
            ["zcli", "login", settings.zerops_token],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise ZcliError(
                "zcli login failed — check ZEROPS_TOKEN: "
                + (proc.stderr.strip() or proc.stdout.strip())[:300]
            )
        self._logged_in = True

    def _run(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        self._ensure_login()
        return subprocess.run(["zcli", *args], capture_output=True, text=True, timeout=timeout)

    def create_project(self, import_yaml: str, project_name: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(import_yaml)
            path = fh.name

        proc = self._run(["project", "project-import", path], timeout=settings.provision_timeout_s)
        Path(path).unlink(missing_ok=True)

        if proc.returncode != 0:
            raise ZcliError(f"import failed: {proc.stderr.strip() or proc.stdout.strip()}")

        # Project id shape is confirmed during the spike; adjust the pattern.
        match = re.search(r"\b([A-Za-z0-9_-]{16,})\b", proc.stdout)
        if not match:
            raise ZcliError(f"could not parse project id from: {proc.stdout[:300]}")
        return match.group(1)

    def deploy(self, project_id: str, repo_url: str, zerops_yaml: str) -> DeployResult:
        """If Import YAML builds straight from git, this is a poll-only step.
        Otherwise clone, write zerops.yaml into the tree, and `zcli service push`.
        """
        deadline = time.time() + settings.deploy_timeout_s
        while time.time() < deadline:
            proc = self._run(["service", "list", "--project-id", project_id], timeout=30)
            output = proc.stdout.lower()
            if "failed" in output or "error" in output:
                logs = self.logs(project_id)
                return DeployResult(
                    ok=False, phase="runtime", project_id=project_id,
                    logs=logs, error=_first_error(logs) or "deployment reported failure",
                )
            if "active" in output or "running" in output:
                return DeployResult(
                    ok=True, phase="runtime", project_id=project_id,
                    logs=self.logs(project_id), verification=self.verify(project_id),
                )
            time.sleep(5)

        # Circuit breaker: never let a hung deploy hold a worker slot.
        return DeployResult(
            ok=False, phase="timeout", project_id=project_id,
            logs=self.logs(project_id),
            error=f"deployment did not settle within {settings.deploy_timeout_s}s",
        )

    def logs(self, project_id: str, service: str = "") -> str:
        args = ["service", "log", "--project-id", project_id, "--limit", "200"]
        if service:
            args += ["--service-id", service]
        try:
            proc = self._run(args, timeout=45)
            return (proc.stdout or proc.stderr)[-8000:]
        except Exception as exc:  # noqa: BLE001
            return f"[zeroth] could not retrieve logs: {exc}"

    def verify(self, project_id: str, service: str = "") -> dict:
        return {"source": "zcli", "project": project_id}

    def destroy(self, project_id: str) -> None:
        try:
            self._run(["project", "delete", "--project-id", project_id, "--confirm"], timeout=90)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass


def _first_error(logs: str) -> str:
    for line in logs.splitlines():
        if re.search(r"error|exception|failed|refused|denied", line, re.I):
            return line.strip()[:400]
    return ""
