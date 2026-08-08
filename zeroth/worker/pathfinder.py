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


def run(job_id: str, repo_url: str, manifest: dict, on_event) -> PathfinderResult:
    provider = get_provider()
    attempts: list[Attempt] = []
    current = manifest
    live_url = ""

    for attempt_no in range(1, settings.max_attempts + 1):
        on_event(
            "attempt_started",
            {"attempt": attempt_no, "provider": provider.name},
        )
        project_id = ""
        started = time.time()

        try:
            import_yaml = render_import_yaml(current, repo_url)
            zerops_yaml = render_zerops_yaml(current, repo_url)

            on_event("stage", {"stage": "provisioning", "attempt": attempt_no})
            project_name = f"zeroth-{job_id[:8]}-{attempt_no}"
            project_id = provider.create_project(import_yaml, project_name)

            on_event("stage", {"stage": "deploying", "attempt": attempt_no,
                               "project_id": project_id})
            result = provider.deploy(project_id, repo_url, zerops_yaml)

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
                })
                return PathfinderResult(True, current, attempts, live_url)

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
                failure_class="infrastructure", failure_message=str(exc)[:800],
                project_id=project_id,
            ))
            on_event("attempt_failed", {
                "attempt": attempt_no,
                "failure_class": "infrastructure",
                "error": str(exc)[:800],
            })
            break

        finally:
            # Teardown always runs. An orphaned project costs credits and quota.
            if project_id:
                provider.destroy(project_id)
                on_event("torn_down", {"attempt": attempt_no, "project_id": project_id})

    return PathfinderResult(False, current, attempts, live_url)
