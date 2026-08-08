import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from zeroth.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Phase A ends at "ready": configuration exists and is shown to the user, and
# nothing has been provisioned. Verification is a separate, explicitly
# requested phase - "ready" is a terminal state for a job nobody asks to verify.
JOB_STATES = (
    "queued", "validating", "ingesting", "analyzing",
    "checking", "generating", "ready", "verifying", "repairing", "done", "failed",
)

# Where an explicitly requested verification run is sent.
VERIFY_TARGETS = ("ephemeral", "account")

# Three failure classes: schema (caught locally, no provisioning),
# infrastructure (import rejected), runtime (built but did not come up).
FAILURE_CLASSES = ("schema", "infrastructure", "runtime", "timeout", "none")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    repo_url: Mapped[str] = mapped_column(String(500))
    repo_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage_detail: Mapped[str] = mapped_column(String(300), default="")
    fingerprint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Whether the repository can be deployed as-is, decided from the fingerprint
    # before any configuration is generated. See worker/compatibility.py.
    compatibility: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    is_gallery: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Verification is opt-in and recorded separately from the generate phase, so
    # a "ready" job stays distinguishable from one that was tried and failed.
    verify_target: Mapped[str] = mapped_column(String(20), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    live_url: Mapped[str] = mapped_column(String(500), default="")
    # Only set for target="account": that project is deliberately NOT torn down,
    # so the id is the user's handle on what Zeroth left behind.
    kept_project_id: Mapped[str] = mapped_column(String(100), default="")

    runs: Mapped[list["Run"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Run.attempt_no"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Run(Base):
    """One deployment attempt. Attempt 1 is initial config; 2+ are repairs."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="running")
    phase: Mapped[str] = mapped_column(String(32), default="schema")
    failure_class: Mapped[str] = mapped_column(String(32), default="none")
    failure_message: Mapped[str] = mapped_column(Text, default="")
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    patch_summary: Mapped[str] = mapped_column(Text, default="")
    zerops_project_id: Mapped[str] = mapped_column(String(100), default="")
    build_log: Mapped[str] = mapped_column(Text, default="")
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="runs")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    filename: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="artifacts")
