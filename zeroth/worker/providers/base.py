"""Transport abstraction for talking to Zerops.

The spike decides which implementation wins (ZCP MCP, zCLI, or REST). Nothing
above this interface knows or cares which one is in use — swap the factory,
not the pipeline.
"""
from dataclasses import dataclass, field
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

    def deploy(self, project_id: str, repo_url: str, zerops_yaml: str) -> DeployResult:
        """Build and deploy the application. Returns success plus evidence."""

    def logs(self, project_id: str, service: str = "") -> str:
        ...

    def verify(self, project_id: str, service: str = "") -> dict:
        ...

    def destroy(self, project_id: str) -> None:
        """Must be safe to call twice, and must never raise."""
