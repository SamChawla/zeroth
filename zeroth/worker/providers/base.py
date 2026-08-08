"""Transport abstraction for talking to Zerops.

The spike decides which implementation wins (ZCP MCP, zCLI, or REST). Nothing
above this interface knows or cares which one is in use — swap the factory,
not the pipeline.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class DeployResult:
    ok: bool
    phase: str  # infrastructure | runtime
    project_id: str = ""
    url: str = ""
    logs: str = ""
    error: str = ""
    verification: dict = field(default_factory=dict)


class ZeropsProvider(Protocol):
    name: str

    def create_project(self, import_yaml: str, project_name: str) -> str:
        """Provision from Import YAML. Returns a project id."""

    def deploy(self, project_id: str, repo_dir: Path, zerops_yaml: str,
               targets: list[tuple[str, str]] | None = None) -> DeployResult:
        """Build and deploy the application from a local working copy.

        Confirmed 2026-08-08 against a real project: Import YAML's
        buildFromGit creates services but does not build them - they sit at
        READY_TO_DEPLOY until something explicitly triggers a build, and
        zcli's own build/deploy commands only work against local files
        (--path-to-file-or-dir / --working-dir), not the git URL. repo_dir
        is the already-cloned target repository; the provider writes
        zerops_yaml into it and deploys from there.
        """

    def logs(self, project_id: str, service: str = "") -> str:
        ...

    def verify(self, project_id: str, service: str = "") -> dict:
        ...

    def destroy(self, project_id: str) -> None:
        """Must be safe to call twice, and must never raise."""
