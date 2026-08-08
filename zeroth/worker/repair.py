"""Failure diagnosis and targeted config repair.

Repairs the manifest, never the application source. Zeroth's claim is that it
finds a working deployment configuration — not that it rewrites your code.
"""
from zeroth.llm import complete_json
from zeroth.worker.analyze import validate

SYSTEM = """You repair Zerops deployment configuration.

You are given a deployment manifest and the failure it produced. Change the
smallest number of fields that could plausibly fix the failure, and return the
complete corrected manifest.

Rules:
- Do not add or remove services unless the failure clearly demands it.
- Do not change service types unless the failure is a runtime version mismatch.
- Services reach each other by hostname on the private network. 'localhost'
  in a connection string is almost always the bug.
- Keep every existing "reason" field intact.
- Return the full manifest JSON, not a diff."""

USER = """MANIFEST:
{manifest}

FAILURE CLASS: {failure_class}
ERROR: {error}

BUILD AND RUNTIME LOG (tail):
{logs}

Return JSON:
{{
  "diagnosis": "one sentence: what actually went wrong",
  "patch_summary": "one sentence: what you changed",
  "confidence": 0.0,
  "manifest": {{ ...corrected manifest... }}
}}"""


class RepairError(Exception):
    pass


def classify(error: str, phase: str) -> str:
    """Three failure classes drive both the UI and the repair strategy."""
    if phase == "timeout":
        return "timeout"
    if phase == "schema":
        return "schema"
    if phase == "infrastructure":
        return "infrastructure"
    return "runtime"


def repair(manifest: dict, failure_class: str, error: str, logs: str) -> dict:
    import json

    result = complete_json(
        SYSTEM,
        USER.format(
            manifest=json.dumps(manifest, indent=2)[:4000],
            failure_class=failure_class,
            error=error[:1000],
            logs=(logs or "")[-3000:],
        ),
        max_tokens=3000,
    )

    patched = result.get("manifest")
    if not isinstance(patched, dict):
        raise RepairError("repair did not return a manifest")

    errors = validate(patched)
    if errors:
        raise RepairError("repaired manifest is invalid: " + "; ".join(errors))

    return {
        "manifest": patched,
        "diagnosis": str(result.get("diagnosis", ""))[:500],
        "patch_summary": str(result.get("patch_summary", ""))[:500],
        "confidence": float(result.get("confidence") or 0.0),
    }
