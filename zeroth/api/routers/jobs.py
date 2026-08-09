from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from zeroth import bus
from zeroth.config import settings
from zeroth.db import get_session
from zeroth.models import Job
from zeroth.safety import RepoRejected, normalise, preflight_size
from zeroth.schemas import JobCreate, JobOut, VerifyRequest

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def client_ip(request: Request) -> str:
    """The actual caller, not the platform proxy in front of us.

    Every request arrives from Zerops' load balancer, so request.client.host is
    the same private address for the entire internet - keying a per-IP limit on
    it puts every visitor in one shared bucket.

    X-Forwarded-For is "client, proxy1, proxy2 ..." where each hop appends the
    address it received the request from. The entry OUR proxy appended is the
    rightmost one, and it is the only entry a caller cannot forge by sending
    the header themselves, so that is the one to trust.
    """
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if hops:
        return hops[-1]
    return request.client.host if request.client else "unknown"


@router.post("", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, request: Request, db: Session = Depends(get_session)):
    ip = client_ip(request)
    if bus.rate_limited(ip):
        raise HTTPException(
            429,
            "You have hit the hourly limit. Browse the gallery for completed runs.",
        )

    # Validate before the job exists. An unsupported host, a malformed path or
    # an oversized repository is knowable now, and answering here means the
    # caller gets the real reason instead of watching a queued run fail.
    try:
        _, owner, repo = normalise(body.repo_url)
        preflight_size(owner, repo)
    except RepoRejected as exc:
        raise HTTPException(400, str(exc)) from exc

    job = Job(repo_url=body.repo_url.strip(), repo_name=f"{owner}/{repo}")

    if body.llm_api_key:
        provider = body.llm_provider or "custom"
        if provider == "custom" and not (body.llm_base_url and body.llm_model):
            raise HTTPException(400, "A custom BYOK provider needs both a base URL and a model.")
        job.llm_provider = provider  # non-secret label; the key goes to Valkey only
    db.add(job)
    db.commit()
    if body.llm_api_key:
        bus.stash_llm(job.id, {
            "provider": job.llm_provider,
            "api_key": body.llm_api_key.strip(),
            "model": (body.llm_model or "").strip(),
            "base_url": (body.llm_base_url or "").strip(),
        })
    bus.rate_consume(ip)
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

    # Honour the deployability verdict. Deploying something the checker already
    # said will not work costs minutes and credits to reproduce a known answer,
    # so "not deployable" is refused outright and "needs changes" requires the
    # caller to have seen the findings and said yes anyway.
    compat = job.compatibility or {}
    deployable = compat.get("deployable") or (
        "no" if compat.get("verdict") == "unsupported" else "with_ack")
    if deployable == "no":
        raise HTTPException(
            409,
            "The deployability check found problems that make this deployment "
            "certain to fail, so running it is disabled. Fix the findings - the "
            "check hands you the exact prompt - and analyze again.",
        )
    if deployable == "with_ack" and not body.acknowledge:
        changes = [f for f in (job.compatibility or {}).get("findings", [])
                   if f.get("level") == "change"]
        raise HTTPException(
            409,
            f"The deployability check found {len(changes)} change(s) that will most "
            f"likely make this deployment fail. Review them, then deploy anyway if "
            f"you want to see it happen.",
        )

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

    ip = client_ip(request)
    if bus.rate_limited(ip):
        raise HTTPException(429, "You have hit the hourly limit. Try again later.")
    bus.rate_consume(ip)

    job.status = "queued"
    job.stage_detail = "Queued for verification"
    job.verify_target = body.target
    job.config_source = body.config_source
    job.error = ""
    db.commit()
    bus.enqueue(job.id, task="verify", target=body.target)
    return job
