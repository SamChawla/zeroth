"""Worker loop: consume jobs, run the pipeline, publish every transition."""
import logging
import signal
import sys
from datetime import datetime, timezone

from zeroth import bus, events, llm
from zeroth.config import settings
from zeroth.db import SessionLocal, init_db
from zeroth.models import Artifact, Job, Run
from zeroth.safety import RepoRejected, normalise, preflight_size
from zeroth.worker import ingest, pathfinder
from zeroth.worker.analyze import analyze
from zeroth.worker.compatibility import assess
from zeroth.worker.generate import check_constraints
from zeroth.worker.recipes import fetch_official
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
    events.record(job.id, "status", {"status": status, "detail": detail})


def process_analyze(job_id: str) -> None:
    """Phase A: read the repository and write the configuration.

    Provisions nothing and costs no credits, so it can finish in seconds and
    hand the user something to read. Deploying it is a separate, opt-in phase.
    """
    db = SessionLocal()
    repo_dir = None
    llm.set_run_context(bus.get_llm(job_id))
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
        events.record(job.id, "fingerprint", {"fingerprint": job.fingerprint})

        # Answer "can this be deployed at all?" before spending a model call on
        # how. Deterministic and free, so it costs nothing to always run it.
        _set_status(db, job, "checking", "Checking deployability")
        report = assess(fp)
        job.compatibility = report.to_dict()
        db.commit()
        events.record(job.id, "compatibility", {"compatibility": job.compatibility})

        _set_status(db, job, "analyzing", "Deciding the architecture")
        manifest = analyze(fp)
        job.manifest = manifest
        db.commit()
        events.record(job.id, "manifest", {"manifest": manifest})

        _set_status(db, job, "generating", "Writing Zerops configuration")
        # A repository that already deploys to Zerops is better evidence than
        # anything generated, so keep its file rather than discarding it.
        own = _repo_config(repo_dir)
        if own:
            _save(db, job, "repo_zerops_yaml", "zerops.yaml (from repository)", own)
        # Their zerops.yaml describes setups against THEIR services, so it only
        # makes sense alongside their import.yaml. Verifying one without the
        # other deploys setup names into a project that has no such services.
        own_import = _repo_import(repo_dir)
        if own_import:
            _save(db, job, "repo_import_yaml", "import.yaml (from repository)", own_import)

        # The platform publishes a recipe per stack. It is the most
        # authoritative starting point available, so offer it as a candidate -
        # but only after it passes the same constraint check as everything
        # else, because the recipes are configurations for their own demo apps
        # and some of them do not satisfy the rules.
        fp_dict = job.fingerprint or {}
        official = fetch_official(fp_dict.get("language") or "", fp_dict.get("framework") or "")
        if official and not check_constraints(official["zerops_yml"]):
            _save(db, job, "official_zerops_yaml",
                  f"zerops.yml ({official['repo']})", official["zerops_yml"])
            if official.get("import_yaml"):
                _save(db, job, "official_import_yaml",
                      f"import.yaml ({official['repo']})", official["import_yaml"])

        framework = (job.fingerprint or {}).get("framework") or ""
        import_yaml = render_import_yaml(manifest, job.repo_url)
        zerops_yaml = render_zerops_yaml(manifest, job.repo_url, framework)
        _save(db, job, "import_yaml", "zerops-project-import.yaml", import_yaml)
        _save(db, job, "zerops_yaml", "zerops.yaml", zerops_yaml)
        events.record(job.id, "config", {
            "import_yaml": import_yaml, "zerops_yaml": zerops_yaml,
        })

        _write_report(db, job, manifest)
        job.finished_at = datetime.now(timezone.utc)
        _set_status(db, job, "ready", "Configuration ready — review it, then try it out.")
        events.record(job.id, "ready", {
            "verifiable": settings.pathfinder_provider != "off",
            "has_own_config": bool(own),
            "has_official_recipe": bool(_artifact(db, job, "official_zerops_yaml")),
        })

    except (RepoRejected, Exception) as exc:  # noqa: BLE001
        _fail(db, job_id, exc)
    finally:
        llm.clear_run_context()
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
    llm.set_run_context(bus.get_llm(job_id))
    try:
        job = db.get(Job, job_id)
        if not job or not job.manifest:
            return

        token = bus.take_token(job_id) if target == "account" else ""
        if target == "account" and not token:
            _set_status(db, job, "ready", "That verification request expired — start it again.")
            events.record(job.id, "verify_rejected", {"reason": "token_expired"})
            return

        slot = bus.acquire_run_slot()
        if not slot:
            _set_status(db, job, "ready", "Verification queue is full — try again shortly.")
            events.record(job.id, "verify_rejected", {"reason": "at_capacity"})
            return

        job.verify_target = target
        db.commit()
        _set_status(db, job, "verifying", f"Deploying to {_target_phrase(target)}")

        clone_url, _, _ = normalise(job.repo_url)
        repo_dir = ingest.clone(clone_url)

        provider = get_provider(token=token)
        # When the application lives in a subdirectory, that subdirectory IS
        # the project as far as building and deploying are concerned.
        subdir = (job.fingerprint or {}).get("project_subdir") or ""
        if subdir and (repo_dir / subdir).is_dir():
            repo_dir = repo_dir / subdir
        source = job.config_source
        override = import_override = ""
        if source == "repository":
            override = _artifact(db, job, "repo_zerops_yaml")
            import_override = _artifact(db, job, "repo_import_yaml")
        elif source == "official":
            override = _artifact(db, job, "official_zerops_yaml")
            import_override = _artifact(db, job, "official_import_yaml")
        # The deploy ahead can run for many minutes. Any read since the last
        # commit has opened a transaction, and Postgres kills connections that
        # sit idle in one - after which every write here fails, including the
        # one in the error handler, and the job hangs in "verifying" forever.
        # Close the transaction now and start fresh afterwards.
        db.commit()

        result = pathfinder.run(
            job.id, job.repo_url, repo_dir, job.manifest,
            lambda ev, payload: events.record(job.id, ev, payload),
            provider=provider,
            keep_project=(target == "account"),
            zerops_yaml_override=override,
            import_yaml_override=import_override,
            framework=(job.fingerprint or {}).get("framework") or "",
        )

        # The connection may have died while the deploy ran; start clean
        # rather than inheriting a poisoned session.
        db.rollback()
        job = db.get(Job, job_id)

        manifest = result.manifest
        job.manifest = manifest
        job.provider = provider.name
        job.verified = result.verified
        # The simulator invents a plausible URL. Publishing it would hand the
        # user a link to a host that does not exist, so a run that provisioned
        # nothing gets no address to click.
        job.live_url = "" if _is_simulated(provider) else result.live_url
        if target == "account":
            job.kept_project_id = result.project_id
        _persist_attempts(db, job, result.attempts)

        if result.verified and source == "generated":
            # Re-stamp the artifacts as verified. Only for the generated config:
            # a repository-config verification proved THEIR files, and there is
            # nothing of ours to restamp. And never let this bookkeeping fail
            # the run - the verification already happened; a rendering problem
            # here is a footnote, not a verdict.
            try:
                framework = (job.fingerprint or {}).get("framework") or ""
                import_yaml = render_import_yaml(manifest, job.repo_url, verified=True)
                zerops_yaml = render_zerops_yaml(manifest, job.repo_url, framework)
                _save(db, job, "import_yaml", "zerops-project-import.yaml", import_yaml)
                _save(db, job, "zerops_yaml", "zerops.yaml", zerops_yaml)
                events.record(job.id, "config", {
                    "import_yaml": import_yaml, "zerops_yaml": zerops_yaml,
                })
            except Exception:  # noqa: BLE001
                log.exception("could not restamp artifacts for job %s", job_id)

        simulated = _is_simulated(provider)
        _write_report(db, job, manifest, result.verified, result.attempts, simulated)
        job.finished_at = datetime.now(timezone.utc)
        _set_status(db, job, "done", _headline(result.verified, result.attempts, simulated)[0])
        events.record(job.id, "complete", {
            "verified": result.verified,
            "live_url": job.live_url,
            "kept_project_id": job.kept_project_id,
            "provider": provider.name,
            "simulated": simulated,
        })
        bus.drop_llm(job.id)

    except (RepoRejected, Exception) as exc:  # noqa: BLE001
        _fail(db, job_id, exc)
    finally:
        llm.clear_run_context()
        if slot:
            bus.release_run_slot()
        if repo_dir:
            ingest.cleanup(repo_dir)
        if provider is not None:
            close = getattr(provider, "close", None)
            if close:
                close()
        db.close()


def _repo_import(repo_dir) -> str:
    """The repository's own project import, if it ships one."""
    for name in ("import.yaml", "import.yml", "zerops-project-import.yaml"):
        path = repo_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _repo_config(repo_dir) -> str:
    """The repository's own zerops config, if it ships one."""
    for name in ("zerops.yaml", "zerops.yml"):
        path = repo_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _artifact(db, job: Job, kind: str) -> str:
    row = db.query(Artifact).filter_by(job_id=job.id, kind=kind).first()
    return row.content if row else ""


def _target_phrase(target: str) -> str:
    return "your Zerops account" if target == "account" else "a throwaway project"


def _is_simulated(provider) -> bool:
    return getattr(provider, "name", "") == "simulated"


def _headline(verified: bool, attempts, simulated: bool = False) -> tuple[str, str]:
    if verified and simulated:
        # The word "verified" is the product's entire claim. A run that
        # provisioned nothing has not earned it, whatever the pipeline returned.
        return (
            "Simulated — nothing was deployed.",
            "This run used the offline provider, so no Zerops project was created and "
            "nothing was built. The configuration below is generated, not proven. Set "
            "ZCLI_TOKEN and PATHFINDER_PROVIDER=zcli to deploy for real.",
        )
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


def _write_report(db, job: Job, manifest: dict, verified: bool = False, attempts=(),
                  simulated: bool = False) -> None:
    runs = db.query(Run).filter_by(job_id=job.id).order_by(Run.attempt_no).all()
    report = render_report(job, job.fingerprint, manifest, runs,
                           _headline(verified, attempts, simulated))
    _save(db, job, "deployment_md", "DEPLOYMENT.md", report)


def _fail(db, job_id: str, exc: Exception) -> None:
    log.exception("job %s failed", job_id)
    # The session may be unusable - a dead connection is a common way to get
    # here. Rolling back first is what lets the failure actually be recorded
    # instead of raising a second time and stranding the job mid-status.
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    job = db.get(Job, job_id)
    if not job:
        return
    job.error = str(exc)[:1000]
    job.finished_at = datetime.now(timezone.utc)
    _set_status(db, job, "failed", str(exc)[:300])
    events.record(job.id, "complete", {"verified": False})
    bus.drop_llm(job_id)


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
