"""Valkey-backed job queue + event fan-out.

The worker publishes every state transition; the API subscribes and relays
over SSE. Nothing polls the database.
"""
import json

import redis

from zeroth.config import settings

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.valkey_url, decode_responses=True)
    return _client


def enqueue(job_id: str) -> None:
    client().rpush(settings.queue_key, job_id)


def dequeue(timeout: int = 5) -> str | None:
    item = client().blpop(settings.queue_key, timeout=timeout)
    return item[1] if item else None


def channel(job_id: str) -> str:
    return f"{settings.events_channel_prefix}{job_id}"


def publish(job_id: str, event: str, payload: dict) -> None:
    body = json.dumps({"event": event, **payload})
    r = client()
    r.publish(channel(job_id), body)
    # Replay buffer: a browser that connects mid-run still sees what it missed.
    r.rpush(f"{channel(job_id)}:log", body)
    r.expire(f"{channel(job_id)}:log", 86400)


def replay(job_id: str) -> list[str]:
    return client().lrange(f"{channel(job_id)}:log", 0, -1)


def rate_limited(ip: str) -> bool:
    key = f"zeroth:rl:{ip}"
    r = client()
    count = r.incr(key)
    if count == 1:
        r.expire(key, 3600)
    return count > settings.rate_limit_per_hour


def acquire_run_slot() -> bool:
    """Global concurrency cap so a burst of judges cannot drain credits."""
    r = client()
    current = r.incr("zeroth:runs:active")
    if current > settings.max_concurrent_runs:
        r.decr("zeroth:runs:active")
        return False
    r.expire("zeroth:runs:active", 1800)
    return True


def release_run_slot() -> None:
    r = client()
    if int(r.get("zeroth:runs:active") or 0) > 0:
        r.decr("zeroth:runs:active")
