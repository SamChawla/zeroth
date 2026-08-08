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
from zeroth.worker.compatibility import assess
from zeroth.worker.fingerprint import build as build_fingerprint
from zeroth.worker.generate import render_import_yaml, render_report, render_zerops_yaml
from zeroth.worker.providers import get_provider

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


def process_analyze(job_id: str) -> None:
    """Phase A: read the repository and write the configuration.

    Provisions nothing and costs no credits, so it can finish in seconds and
    hand the user something to read. Deploying it is a separate, opt-in phase.
    """
    db = SessionLocal()
    repo_dir = None
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

        # Answer "can this be deployed at all?" before spending a model call on
        # how. Deterministic and free, so it costs nothing to always run it.
        _set_status(db, job, "checking", "Checking deployability")
        report = assess(fp)
        job.compatibility = report.to_dict()
        db.commit()
        bus.publish(job.id, "compatibility", {"compatibility": job.compatibility})

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

        _write_report(db, job, manifest)
        job.finished_at = datetime.now(timezone.utc)
        _set_status(db, job, "ready", "Configuration ready — review it, then try it out.")
        bus.publish(job.id, "ready", {
            "verifiable": settings.pathfinder_provider != "off",
        })

    except (RepoRejected, Exception) as exc:  # noqa: BLE001
        _fail(db, job_id, exc)
    finally:
        if repo_dir:
            ingest.cleanup(repo_dir)
        db.close()


def process_verify(job_id: str, target: str) -> None:
    """Phase B: prove the configuration by deploying it. Only runs when asked.

    target="ephemeral" provisions a throwaway project on Zeroth's own account
    and always tears it down. target="account" deploys into the user's account
    using a token they supplied for this request alone, and deliberately leaves
    the project standing - tearing down someone's own project would be the
    opposite of what they asked for.
    """
    db = SessionLocal()
    repo_dir = None
    provider = None
    slot = False
    try:
        job = db.get(Job, job_id)
        if not job or not job.manifest:
            return

        token = bus.take_token(job_id) if target == "account" else ""
        if target == "account" and not token:
            _set_status(db, job, "ready", "That verification request expired — start it again.")
            bus.publish(job.id, "verify_rejected", {"reason": "token_expired"})
            return

        slot = bus.acquire_run_slot()
        if not slot:
            _set_status(db, job, "ready", "Verification queue is full — try again shortly.")
            bus.publish(job.id, "verify_rejected", {"reason": "at_capacity"})
            return

        job.verify_target = target
        db.commit()
        _set_status(db, job, "verifying", f"Deploying to {_target_phrase(target)}")

        clone_url, _, _ = normalise(job.repo_url)
        repo_dir = ingest.clone(clone_url)

        provider = get_provider(token=token)
        result = pathfinder.run(
            job.id, job.repo_url, repo_dir, job.manifest,
            lambda ev, payload: bus.publish(job.id, ev, payload),
            provider=provider,
            keep_project=(target == "account"),
        )

        manifest = result.manifest
        job.manifest = manifest
        job.verified = result.verified
        job.live_url = result.live_url
        if target == "account":
            job.kept_project_id = result.project_id
        _persist_attempts(db, job, result.attempts)

        if result.verified:
            import_yaml = render_import_yaml(manifest, job.repo_url, verified=True)
            zerops_yaml = render_zerops_yaml(manifest, job.repo_url)
            _save(db, job, "import_yaml", "zerops-project-import.yaml", import_yaml)
            _save(db, job, "zerops_yaml", "zerops.yaml", zerops_yaml)
            bus.publish(job.id, "config", {
                "import_yaml": import_yaml, "zerops_yaml": zerops_yaml,
            })

        _write_report(db, job, manifest, result.verified, result.attempts)
        job.finished_at = datetime.now(timezone.utc)
        _set_status(db, job, "done", _headline(result.verified, result.attempts)[0])
        bus.publish(job.id, "complete", {
            "verified": result.verified,
            "live_url": result.live_url,
            "kept_project_id": job.kept_project_id,
        })

    except (RepoRejected, Exception) as exc:  # noqa: BLE001
        _fail(db, job_id, exc)
    finally:
        if slot:
            bus.release_run_slot()
        if repo_dir:
            ingest.cleanup(repo_dir)
        if provider is not None:
            close = getattr(provider, "close", None)
            if close:
                close()
        db.close()


def _target_phrase(target: str) -> str:
    return "your Zerops account" if target == "account" else "a throwaway project"


def _headline(verified: bool, attempts) -> tuple[str, str]:
    if verified:
        return (
            "Verified — this configuration was deployed and came up.",
            f"Zeroth deployed this repository and confirmed it started after "
            f"{len(attempts)} attempt(s).",
        )
    if attempts:
        return (
            "Not verified — the deployment did not come up.",
            "Review the attempt history below before deploying this yourself.",
        )
    return (
        "Generated, not verified.",
        "Nothing has been provisioned. Run a verification to prove it boots.",
    )


def _write_report(db, job: Job, manifest: dict, verified: bool = False, attempts=()) -> None:
    runs = db.query(Run).filter_by(job_id=job.id).order_by(Run.attempt_no).all()
    report = render_report(job, job.fingerprint, manifest, runs,
                           _headline(verified, attempts))
    _save(db, job, "deployment_md", "DEPLOYMENT.md", report)


def _fail(db, job_id: str, exc: Exception) -> None:
    log.exception("job %s failed", job_id)
    job = db.get(Job, job_id)
    if not job:
        return
    job.error = str(exc)[:1000]
    job.finished_at = datetime.now(timezone.utc)
    _set_status(db, job, "failed", str(exc)[:300])
    bus.publish(job.id, "complete", {"verified": False})


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
        task = bus.dequeue(timeout=5)
        if not task:
            continue
        job_id, kind = task["job"], task.get("task", "analyze")
        log.info("picked up job %s (%s)", job_id, kind)
        if kind == "verify":
            process_verify(job_id, task.get("target", "ephemeral"))
        else:
            process_analyze(job_id)
    log.info("worker stopped")


if __name__ == "__main__":
    sys.exit(main())
