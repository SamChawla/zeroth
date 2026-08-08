from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from zeroth import bus
from zeroth.db import get_session
from zeroth.models import Job
from zeroth.safety import RepoRejected, normalise
from zeroth.schemas import JobCreate, JobOut

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
