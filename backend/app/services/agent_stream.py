"""Replay committed, safe execution events without holding a streaming transaction."""

import asyncio
import json
import logging
from time import monotonic

from app.core.database import SessionLocal
from app.models.agent import AgentEvent
from app.services.agent_runs import TERMINAL, require_run, run_output
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15


def read_progress(project_id, run_id, after):
    with SessionLocal() as db:
        run = require_run(db, project_id, run_id)
        values = db.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id, AgentEvent.sequence > after)
            .order_by(AgentEvent.sequence)
            .limit(100)
        ).all()
        state = run_output(run)
        # Push only explicit public state, never raw tool output, prompts or reasoning.
        state = {
            key: state[key]
            for key in (
                "status",
                "cancel_requested",
                "plan",
                "usage",
                "tool_count",
                "error",
                "started_at",
                "finished_at",
            )
        }
        events = [
            {
                "sequence": v.sequence,
                "kind": v.kind,
                "message": v.message,
                "created_at": v.created_at,
            }
            for v in values
        ]
        return jsonable_encoder(state), jsonable_encoder(events)


def frame(kind, data, sequence=None):
    identifier = f"id: {sequence}\n" if sequence is not None else ""
    return f"{identifier}event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_progress(request, project_id, run_id, after):
    last_state = None
    heartbeat = monotonic()
    logger.info("event=agent_stream_open run_id=%s", run_id)
    try:
        yield "retry: 1500\n\n"
        while not await request.is_disconnected():
            # Each read owns a short session; no connection/row lock survives a yield.
            state, events = await run_in_threadpool(read_progress, project_id, run_id, after)
            for event in events:
                after = event["sequence"]
                yield frame("progress", event, after)
            if state != last_state:
                yield frame("state", state)
                last_state = state
            if state["status"] in TERMINAL and len(events) < 100:
                yield frame("complete", {"status": state["status"], "sequence": after})
                return
            if monotonic() - heartbeat >= HEARTBEAT_SECONDS:
                yield frame("heartbeat", {"sequence": after})
                heartbeat = monotonic()
            if len(events) < 100:
                await asyncio.sleep(POLL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "event=agent_stream_unavailable run_id=%s exception_type=%s", run_id, type(exc).__name__
        )
        yield frame("unavailable", {"message": "实时连接暂不可用，改用定时查询。"})
    finally:
        logger.info("event=agent_stream_closed run_id=%s", run_id)
