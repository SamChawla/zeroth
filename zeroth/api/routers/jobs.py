from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from zeroth import bus
from zeroth.config import settings
from zeroth.db import get_session
from zeroth.models import Job
from zeroth.safety import RepoRejected, normalise
from zeroth.schemas import JobCreate, JobOut, VerifyRequest

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, request: Request, db: Session = Depends(get_session)):
    ip = request.client.host if request.client else "unknown"
    if bus.rate_limited(ip):
        raise HTTPException(
            429,
            "You have hit the hourly limit. Browse the gallery for completed runs.",
        )

    try:
        _, owner, repo = normalise(body.repo_url)
    except RepoRejected as exc:
        raise HTTPException(400, str(exc)) from exc

    job = Job(repo_url=body.repo_url.strip(), repo_name=f"{owner}/{repo}")
    db.add(job)
    db.commit()
    bus.enqueue(job.id)
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_session)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "No job with that id.")
    return job


@router.post("/{job_id}/verify", response_model=JobOut, status_code=202)
def verify_job(
    job_id: str,
    body: VerifyRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    """Deploy an already-generated configuration. Never happens on its own."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "No job with that id.")
    if job.status in ("verifying", "repairing"):
        raise HTTPException(409, "That configuration is already being verified.")
    if not job.manifest or job.status not in ("ready", "done", "failed"):
        raise HTTPException(409, "There is no finished configuration to verify yet.")

    if body.target == "account":
        token = (body.token or "").strip()
        if not token:
            raise HTTPException(
                400, "Deploying to your own account needs a Zerops personal access token."
            )
        # Straight to Valkey under a TTL - deliberately never touches the job row.
        bus.stash_token(job.id, token)
    elif settings.pathfinder_provider == "off":
        raise HTTPException(503, "Throwaway verification is disabled on this instance.")

    ip = request.client.host if request.client else "unknown"
    if bus.rate_limited(ip):
        raise HTTPException(429, "You have hit the hourly limit. Try again later.")

    job.status = "queued"
    job.stage_detail = "Queued for verification"
    job.verify_target = body.target
    job.error = ""
    db.commit()
    bus.enqueue(job.id, task="verify", target=body.target)
    return job
