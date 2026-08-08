"""Offline provider.

Runs the whole pipeline without provisioning anything, so Tier 0 works before
the spike lands and the demo survives an empty credit balance. It simulates a
realistic first-attempt failure so the repair loop can be exercised locally.
"""
import time

from zeroth.worker.providers.base import DeployResult


class SimulatedProvider:
    name = "simulated"

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}

    def create_project(self, import_yaml: str, project_name: str) -> str:
        time.sleep(0.4)
        return f"sim-{project_name}"

    def deploy(self, project_id: str, repo_dir, zerops_yaml: str) -> DeployResult:
        time.sleep(0.8)
        n = self._attempts.get(project_id, 0) + 1
        self._attempts[project_id] = n

        if n == 1:
            return DeployResult(
                ok=False,
                phase="runtime",
                project_id=project_id,
                logs=(
                    "[build] installing dependencies\n"
                    "[build] build succeeded in 41s\n"
                    "[run] starting service\n"
                    "[run] django.db.utils.OperationalError: could not translate "
                    "host name \"localhost\" to address\n"
                    "[run] container exited with code 1\n"
                ),
                error='could not translate host name "localhost" to address',
            )

        return DeployResult(
            ok=True,
            phase="runtime",
            project_id=project_id,
            url=f"https://{project_id}.zerops.app",
            logs="[build] build succeeded in 38s\n[run] listening on 0.0.0.0:8000\n",
            verification={"http": 200, "health": "passed", "errors_in_log": 0},
        )

    def logs(self, project_id: str, service: str = "") -> str:
        return "[simulated] no additional logs"

    def verify(self, project_id: str, service: str = "") -> dict:
        return {"http": 200, "health": "passed"}

    def destroy(self, project_id: str) -> None:
        self._attempts.pop(project_id, None)
