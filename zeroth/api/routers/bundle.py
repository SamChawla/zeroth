import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from zeroth.db import get_session
from zeroth.models import Job

router = APIRouter(prefix="/api/jobs", tags=["bundle"])


@router.get("/{job_id}/bundle")
def bundle(job_id: str, db: Session = Depends(get_session)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "No job with that id.")
    if not job.artifacts:
        raise HTTPException(409, "This job has not produced configuration yet.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in job.artifacts:
            path = (
                f"services/{artifact.filename}"
                if artifact.kind == "zerops_yaml"
                else artifact.filename
            )
            archive.writestr(path, artifact.content)
    buffer.seek(0)

    name = (job.repo_name or "zeroth").replace("/", "-")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-zeroth-bundle.zip"'},
    )
