"""Durable run events: the Pathfinder timeline.

Everything the worker announces already flows through bus.publish for the live
stream, but Valkey's replay buffer expires in a day - so a finished run kept
its verdict and lost its story. record() writes the same event to Postgres
(trimmed: bulky payloads like full YAML and build logs live in artifacts and
runs already) and then publishes the full payload live. One call site, both
audiences.
"""
import logging
from datetime import datetime, timezone

from zeroth import bus
from zeroth.db import SessionLocal
from zeroth.models import RunEvent

log = logging.getLogger("zeroth.events")

# Payload keys that are large and already persisted elsewhere.
_BULKY = {"import_yaml", "zerops_yaml", "logs", "fingerprint", "manifest", "compatibility"}


def record(job_id: str, event: str, payload: dict) -> None:
    slim = {}
    for k, v in (payload or {}).items():
        if k in _BULKY:
            continue
        slim[k] = v if not isinstance(v, str) or len(v) <= 500 else v[:500]

    # Its own short session: the caller's session may be mid-transaction or
    # poisoned, and a timeline write must never take the run down with it.
    try:
        db = SessionLocal()
        try:
            db.add(RunEvent(job_id=job_id, event=event, payload=slim))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        log.exception("could not persist event %s for %s", event, job_id)

    bus.publish(job_id, event, payload)
