"""Worker loop: consume jobs, run the pipeline, publish every transition."""
import logging
import signal
import sys
from datetime import datetime, timezone

from zeroth import bus
from zeroth.config import settings
from zeroth.db import SessionLocal, init_db
from zeroth.models import Artifact, Job, Run
from zeroth.safety import RepoRejected, normalise, preflight_size
from zeroth.worker import ingest, pathfinder
from zeroth.worker.analyze import analyze
from zeroth.worker.fingerprint import build as build_fingerprint
from zeroth.worker.generate import render_import_yaml, render_report, render_zerops_yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zeroth.worker")

_running = True


def _stop(*_):
    global _running
    _running = False
    log.info("shutdown requested")


def _set_status(db, job: Job, status: str, detail: str = "") -> None:
    job.status = status
    job.stage_detail = detail
    db.commit()
    bus.publish(job.id, "status", {"status": status, "detail": detail})


def process(job_id: str) -> None:
    db = SessionLocal()
    repo_dir = None
    slot = False
    try:
        job = db.get(Job, job_id)
        if not job:
            return

        _set_status(db, job, "validating", "Checking the repository")
        clone_url, owner, repo = normalise(job.repo_url)
        preflight_size(owner, repo)
        job.repo_name = f"{owner}/{repo}"
        db.commit()

        _set_status(db, job, "ingesting", f"Cloning {owner}/{repo}")
        repo_dir = ingest.clone(clone_url)

        fp = build_fingerprint(repo_dir, job.repo_name)
        job.fingerprint = fp.to_dict()
        db.commit()
        bus.publish(job.id, "fingerprint", {"fingerprint": job.fingerprint})

        _set_status(db, job, "analyzing", "Deciding the architecture")
        manifest = analyze(fp)
        job.manifest = manifest
        db.commit()
        bus.publish(job.id, "manifest", {"manifest": manifest})

        _set_status(db, job, "generating", "Writing Zerops configuration")
        import_yaml = render_import_yaml(manifest, job.repo_url)
        zerops_yaml = render_zerops_yaml(manifest, job.repo_url)
        _save(db, job, "import_yaml", "zerops-project-import.yaml", import_yaml)
        _save(db, job, "zerops_yaml", "zerops.yaml", zerops_yaml)
        bus.publish(job.id, "config", {
            "import_yaml": import_yaml, "zerops_yaml": zerops_yaml,
        })

        verified = False
        attempts = []
        if settings.pathfinder_provider != "off":
            slot = bus.acquire_run_slot()
            if slot:
                _set_status(db, job, "verifying", "Sending a pathfinder run")
                result = pathfinder.run(
                    job.id, job.repo_url, repo_dir, manifest,
                    lambda ev, payload: bus.publish(job.id, ev, payload),
                )
                verified = result.verified
                attempts = result.attempts
                manifest = result.manifest
                job.manifest = manifest
                _persist_attempts(db, job, attempts)

                if verified:
                    import_yaml = render_import_yaml(manifest, job.repo_url, verified=True)
                    zerops_yaml = render_zerops_yaml(manifest, job.repo_url)
                    _save(db, job, "import_yaml", "zerops-project-import.yaml", import_yaml)
                    _save(db, job, "zerops_yaml", "zerops.yaml", zerops_yaml)
            else:
                bus.publish(job.id, "queued_for_capacity", {
                    "detail": "Verification queue is full; configuration was still generated.",
                })

        headline = (
            "Verified — this configuration was deployed and came up."
            if verified
            else "Generated, not verified."
        )
        detail = (
            f"Zeroth deployed this repository to an ephemeral Zerops project and "
            f"confirmed it started after {len(attempts)} attempt(s)."
            if verified
            else "Review before deploying. See the attempt history below."
        )
        report = render_report(job, job.fingerprint, manifest,
                               db.query(Run).filter_by(job_id=job.id).order_by(Run.attempt_no).all(),
                               (headline, detail))
        _save(db, job, "deployment_md", "DEPLOYMENT.md", report)

        job.finished_at = datetime.now(timezone.utc)
        _set_status(db, job, "done" if verified or not attempts else "done", headline)
        bus.publish(job.id, "complete", {"verified": verified})

    except (RepoRejected, Exception) as exc:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        job = db.get(Job, job_id)
        if job:
            job.error = str(exc)[:1000]
            job.finished_at = datetime.now(timezone.utc)
            _set_status(db, job, "failed", str(exc)[:300])
            bus.publish(job.id, "complete", {"verified": False})
    finally:
        if slot:
            bus.release_run_slot()
        if repo_dir:
            ingest.cleanup(repo_dir)
        db.close()


def _save(db, job: Job, kind: str, filename: str, content: str) -> None:
    existing = db.query(Artifact).filter_by(job_id=job.id, kind=kind).first()
    if existing:
        existing.content = content
    else:
        db.add(Artifact(job_id=job.id, kind=kind, filename=filename, content=content))
    db.commit()


def _persist_attempts(db, job: Job, attempts) -> None:
    for a in attempts:
        db.add(Run(
            job_id=job.id, attempt_no=a.attempt_no, status=a.status, phase=a.phase,
            failure_class=a.failure_class, failure_message=a.failure_message,
            diagnosis=a.diagnosis, patch_summary=a.patch_summary,
            zerops_project_id=a.project_id, build_log=a.logs[-8000:],
            verification=a.verification or None,
            ended_at=datetime.now(timezone.utc),
        ))
    db.commit()


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    init_db()
    log.info("worker ready, provider=%s", settings.pathfinder_provider)
    while _running:
        job_id = bus.dequeue(timeout=5)
        if job_id:
            log.info("picked up job %s", job_id)
            process(job_id)
    log.info("worker stopped")


if __name__ == "__main__":
    sys.exit(main())
