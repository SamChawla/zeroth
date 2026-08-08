from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zeroth.db import get_session
from zeroth.models import Job

router = APIRouter(prefix="/api", tags=["gallery"])


@router.get("/gallery")
def gallery(db: Session = Depends(get_session)):
    """Completed runs, newest first.

    The landing page renders this immediately so a visitor sees finished work
    with real logs before deciding whether to start their own run.
    """
    jobs = (
        db.query(Job)
        .filter(Job.status.in_(["done", "failed"]))
        .order_by(Job.is_gallery.desc(), Job.created_at.desc())
        .limit(12)
        .all()
    )
    return [
        {
            "id": j.id,
            "repo_name": j.repo_name,
            "repo_url": j.repo_url,
            "status": j.status,
            "attempts": len(j.runs),
            "repaired": any(r.status == "failed" for r in j.runs)
            and any(r.status == "passed" for r in j.runs),
            "verified": any(r.status == "passed" for r in j.runs),
            "services": [s.get("hostname") for s in (j.manifest or {}).get("services", [])],
            "framework": (j.fingerprint or {}).get("framework") or (j.fingerprint or {}).get("language"),
            "created_at": j.created_at.isoformat(),
        }
        for j in jobs
    ]
