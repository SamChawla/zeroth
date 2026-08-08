"""The pathfinder run.

A throwaway deployment sent ahead to prove the route before the user commits.
Provision an ephemeral project, deploy, read the evidence, repair on failure,
and always tear down.

Failure is classified into three levels:
  schema         caught locally, costs no provisioning
  infrastructure the import was rejected
  runtime        it built but did not come up
"""
import time

import yaml
from dataclasses import dataclass, field

from zeroth.config import settings
from zeroth.worker.generate import render_import_yaml, render_zerops_yaml
from zeroth.worker.providers import get_provider
from zeroth.worker.repair import classify, repair


@dataclass
class Attempt:
    attempt_no: int
    status: str  # passed | failed
    phase: str
    failure_class: str = "none"
    failure_message: str = ""
    diagnosis: str = ""
    patch_summary: str = ""
    project_id: str = ""
    logs: str = ""
    verification: dict = field(default_factory=dict)


@dataclass
class PathfinderResult:
    verified: bool
    manifest: dict
    attempts: list[Attempt]
    live_url: str = ""
    project_id: str = ""


def _deploy_targets(import_yaml: str, zerops_yaml: str) -> list[tuple[str, str]]:
    """Which service gets deployed with which setup.

    Managed services (databases, caches) are provisioned by the import and have
    nothing to deploy, so only runtimes are returned. A service names its setup
    via zeropsSetup; without one, the convention is a setup matching the service
    hostname, falling back to the only setup defined.
    """
    try:
        services = (yaml.safe_load(import_yaml) or {}).get("services") or []
        setups = [s["setup"] for s in ((yaml.safe_load(zerops_yaml) or {}).get("zerops") or [])]
    except yaml.YAMLError:
        return []
    if not setups:
        return []

    targets = []
    for svc in services:
        # A managed service's type carries a colon (postgresql:single@17) or is
        # a known managed family; those are created, never deployed to.
        stype = str(svc.get("type") or "")
        if ":" in stype or stype.split("@")[0] in {
            "postgresql", "mariadb", "valkey", "keydb", "kafka", "nats",
            "elasticsearch", "meilisearch", "qdrant", "typesense", "clickhouse",
            "object-storage", "shared-storage",
        }:
            continue
        host = svc.get("hostname")
        if not host:
            continue
        setup = svc.get("zeropsSetup") or (host if host in setups else setups[0])
        targets.append((host, setup))
    return targets


def run(
    job_id: str,
    repo_url: str,
    repo_dir,
    manifest: dict,
    on_event,
    provider=None,
    keep_project: bool = False,
    zerops_yaml_override: str = "",
    import_yaml_override: str = "",
    framework: str = "",
) -> PathfinderResult:
    """Deploy the manifest, repairing on failure.

    keep_project applies to the SUCCEEDING attempt only: when the run is
    targeting the user's own account they want what worked left standing.
    Failed attempts are always torn down - abandoning half-built projects in
    somebody's account would be worse than not offering the option at all.
    """
    provider = provider or get_provider()
    attempts: list[Attempt] = []
    current = manifest
    live_url = ""
    kept_project_id = ""

    for attempt_no in range(1, settings.max_attempts + 1):
        on_event(
            "attempt_started",
            {"attempt": attempt_no, "provider": provider.name},
        )
        project_id = ""
        started = time.time()

        try:
            import_yaml = import_yaml_override or render_import_yaml(current, repo_url)
            # Verifying the repository's own configuration means deploying it
            # unchanged - patching it would prove something the repository does
            # not actually claim.
            zerops_yaml = zerops_yaml_override or render_zerops_yaml(current, repo_url, framework)

            on_event("stage", {"stage": "provisioning", "attempt": attempt_no})
            project_name = f"zeroth-{job_id[:8]}-{attempt_no}"
            project_id = provider.create_project(import_yaml, project_name)

            on_event("stage", {"stage": "deploying", "attempt": attempt_no,
                               "project_id": project_id})
            result = provider.deploy(
                project_id, repo_dir, zerops_yaml,
                targets=_deploy_targets(import_yaml, zerops_yaml),
            )

            if result.ok:
                live_url = result.url
                attempts.append(Attempt(
                    attempt_no=attempt_no, status="passed", phase="runtime",
                    project_id=project_id, logs=result.logs,
                    verification=result.verification,
                ))
                on_event("attempt_passed", {
                    "attempt": attempt_no,
                    "elapsed": round(time.time() - started, 1),
                    "verification": result.verification,
                    "url": live_url,
                })
                if keep_project:
                    kept_project_id = project_id
                    project_id = ""  # suppress the teardown in `finally`
                    on_event("kept", {"project_id": kept_project_id, "url": live_url})
                return PathfinderResult(True, current, attempts, live_url, kept_project_id)

            failure_class = classify(result.error, result.phase)
            attempt = Attempt(
                attempt_no=attempt_no, status="failed", phase=result.phase,
                failure_class=failure_class, failure_message=result.error,
                project_id=project_id, logs=result.logs,
            )
            on_event("attempt_failed", {
                "attempt": attempt_no,
                "failure_class": failure_class,
                "error": result.error,
                "logs": result.logs,
            })

            if attempt_no < settings.max_attempts:
                on_event("stage", {"stage": "diagnosing", "attempt": attempt_no})
                try:
                    fix = repair(current, failure_class, result.error, result.logs)
                    attempt.diagnosis = fix["diagnosis"]
                    attempt.patch_summary = fix["patch_summary"]
                    current = fix["manifest"]
                    on_event("repair_proposed", {
                        "attempt": attempt_no,
                        "diagnosis": fix["diagnosis"],
                        "patch_summary": fix["patch_summary"],
                        "confidence": fix["confidence"],
                    })
                except Exception as exc:  # noqa: BLE001
                    attempt.diagnosis = f"repair failed: {exc}"
                    attempts.append(attempt)
                    break

            attempts.append(attempt)

        except Exception as exc:  # noqa: BLE001
            attempts.append(Attempt(
                attempt_no=attempt_no, status="failed", phase="infrastructure",
                # zcli narrates the whole import before failing, so the useful
                # line is at the end of a long message. 800 chars cut it off.
                failure_class="infrastructure", failure_message=str(exc)[-4000:],
                project_id=project_id,
            ))
            on_event("attempt_failed", {
                "attempt": attempt_no,
                "failure_class": "infrastructure",
                "error": str(exc)[:800],
            })
            break

        finally:
            # Teardown always runs for anything still owned here. An orphaned
            # project costs credits and quota; a kept one has already cleared
            # project_id above so it survives this block.
            if project_id:
                provider.destroy(project_id)
                on_event("torn_down", {"attempt": attempt_no, "project_id": project_id})

    return PathfinderResult(False, current, attempts, live_url, kept_project_id)
