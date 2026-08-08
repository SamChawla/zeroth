"""zCLI-backed provider.

Shelling out to zcli is deliberate: it is the interface Zerops documents and
keeps stable, and it avoids hand-rolling auth and polling against the REST API
under a 48-hour clock.

SPIKE-DEPENDENT. Confirm these against your account before enabling:
  1. Does project-import accept a generated file and return a usable id?
  2. Does the import build directly from git, or must we push a working copy?
  3. How does a failing build surface in `zcli service log`?
  4. Does `zcli project delete` free quota immediately?
Fill in the command shapes below once the spike answers them.
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
    name = "zcli"

    def _run(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["zcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"ZEROPS_TOKEN": settings.zerops_token, "PATH": "/usr/local/bin:/usr/bin:/bin"},
        )

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
            proc = self._run(["service", "list", "--projectId", project_id], timeout=30)
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
        args = ["service", "log", "--projectId", project_id, "--limit", "200"]
        if service:
            args += ["--serviceId", service]
        try:
            proc = self._run(args, timeout=45)
            return (proc.stdout or proc.stderr)[-8000:]
        except Exception as exc:  # noqa: BLE001
            return f"[zeroth] could not retrieve logs: {exc}"

    def verify(self, project_id: str, service: str = "") -> dict:
        return {"source": "zcli", "project": project_id}

    def destroy(self, project_id: str) -> None:
        try:
            self._run(["project", "delete", "--projectId", project_id, "--confirm"], timeout=90)
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass


def _first_error(logs: str) -> str:
    for line in logs.splitlines():
        if re.search(r"error|exception|failed|refused|denied", line, re.I):
            return line.strip()[:400]
    return ""
