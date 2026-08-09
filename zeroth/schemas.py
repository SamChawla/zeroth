from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    repo_url: str = Field(min_length=8, max_length=500)
    # BYOK: an OpenAI-compatible key used for THIS run only. Stashed in Valkey
    # under a TTL, read by the worker, deleted when the run settles - never
    # written to Postgres, artifacts or logs. BYOK runs skip the house token
    # budget: the key runs until its owner's provider says stop.
    llm_provider: Literal["openai", "groq", "openrouter", "custom"] | None = None
    llm_api_key: str | None = Field(default=None, max_length=500)
    llm_model: str | None = Field(default=None, max_length=200)
    llm_base_url: str | None = Field(default=None, max_length=300)


class VerifyRequest(BaseModel):
    """Ask for a generated configuration to actually be deployed.

    target="ephemeral" uses Zeroth's own throwaway project and tears it down.
    target="account" deploys into the caller's Zerops account and leaves it
    standing; the token is used for that one run and never stored.
    """

    target: Literal["ephemeral", "account"] = "ephemeral"
    token: str | None = Field(default=None, max_length=500)
    # Set only after the caller has been shown the compatibility findings and
    # chosen to deploy regardless. The deployability check exists to stop us
    # spending minutes and credits proving a failure it already predicted.
    acknowledge: bool = False
    # "repository" verifies the zerops.yaml already in the repo. A repo that
    # ships working config is better evidence than one we wrote.
    config_source: Literal["generated", "repository", "official"] = "generated"


class ArtifactOut(BaseModel):
    kind: str
    filename: str
    content: str

    class Config:
        from_attributes = True


class RunOut(BaseModel):
    attempt_no: int
    status: str
    phase: str
    failure_class: str
    failure_message: str
    diagnosis: str
    patch_summary: str
    build_log: str
    verification: dict | None
    started_at: datetime
    ended_at: datetime | None

    class Config:
        from_attributes = True


class EventOut(BaseModel):
    event: str
    payload: dict | None
    at: datetime

    class Config:
        from_attributes = True


class JobOut(BaseModel):
    id: str
    repo_url: str
    repo_name: str
    status: str
    stage_detail: str
    fingerprint: dict | None
    compatibility: dict | None = None
    manifest: dict | None
    error: str
    created_at: datetime
    finished_at: datetime | None
    verify_target: str = ""
    provider: str = ""
    config_source: str = "generated"
    llm_provider: str = ""
    verified: bool = False
    live_url: str = ""
    kept_project_id: str = ""
    runs: list[RunOut] = []
    events: list[EventOut] = []
    artifacts: list[ArtifactOut] = []

    class Config:
        from_attributes = True
