import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from zeroth import bus
from zeroth.db import get_session
from zeroth.models import Job

router = APIRouter(prefix="/api/jobs", tags=["stream"])

# "ready" is terminal for the stream, not for the job: phase A is over and
# nothing further arrives unless the user asks for a verification, at which
# point the browser opens a fresh stream. Holding the connection open would
# mean every generated-but-unverified job parks a socket indefinitely.
TERMINAL = {"ready", "done", "failed"}
CLOSING_EVENTS = {"complete", "ready"}


@router.get("/{job_id}/events")
async def events(job_id: str, db: Session = Depends(get_session)):
    job = db.get(Job, job_id)
    terminal_now = bool(job and job.status in TERMINAL)

    async def generator():
        # Replay what the browser missed before following the live channel.
        for line in bus.replay(job_id):
            yield f"data: {line}\n\n"

        if terminal_now:
            yield "event: close\ndata: {}\n\n"
            return

        pubsub = bus.client().pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(bus.channel(job_id))
        try:
            while True:
                message = pubsub.get_message(timeout=1.0)
                if message and message.get("data"):
                    payload = message["data"]
                    yield f"data: {payload}\n\n"
                    try:
                        if json.loads(payload).get("event") in CLOSING_EVENTS:
                            # Explicit terminal event; the client closes on this
                            # so the browser does not reconnect forever.
                            yield "event: close\ndata: {}\n\n"
                            return
                    except json.JSONDecodeError:
                        pass
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.15)
        finally:
            pubsub.close()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
