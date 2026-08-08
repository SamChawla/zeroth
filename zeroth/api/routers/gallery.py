from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zeroth.db import get_session
from zeroth.models import Job

router = APIRouter(prefix="/api", tags=["gallery"])

# Kept short deliberately: this is a showcase, not a log. Testing generates runs
# quickly and a wall of them buries the ones worth looking at.
GALLERY_SIZE = 3


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
        .limit(GALLERY_SIZE)
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
            # A simulated run provisions nothing, so it cannot be "verified"
            # here either - the showcase is the product's evidence, and padding
            # it with runs that never deployed would be the lie it exists to
            # disprove.
            "verified": any(r.status == "passed" for r in j.runs) and j.provider != "simulated",
            "simulated": j.provider == "simulated",
            "provider": j.provider,
            "services": [s.get("hostname") for s in (j.manifest or {}).get("services", [])],
            "framework": (j.fingerprint or {}).get("framework") or (j.fingerprint or {}).get("language"),
            "created_at": j.created_at.isoformat(),
        }
        for j in jobs
    ]
