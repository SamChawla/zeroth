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
        # Counted per run, not per project: every attempt provisions a NEW
        # project, so keying this by project id would make attempt 2 look like
        # attempt 1 and the simulated run could never reach a passing state.
        # One provider instance serves one pathfinder run.
        self._attempt = 0

    def create_project(self, import_yaml: str, project_name: str) -> str:
        time.sleep(0.4)
        return f"sim-{project_name}"

    def deploy(self, project_id: str, repo_dir, zerops_yaml: str, targets=None, routes=None) -> DeployResult:
        time.sleep(0.8)
        self._attempt += 1

        if self._attempt == 1:
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

    def await_git_build(self, project_id: str, services, routes=None) -> DeployResult:
        return self.deploy(project_id, None, "")

    def logs(self, project_id: str, service: str = "") -> str:
        return "[simulated] no additional logs"

    def verify(self, project_id: str, service: str = "") -> dict:
        return {"http": 200, "health": "passed"}

    def destroy(self, project_id: str) -> None:
        """Nothing was provisioned, so there is nothing to tear down."""
