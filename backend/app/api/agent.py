import base64
import json
from datetime import date, datetime, time, timedelta
from typing import Literal
from urllib.parse import unquote
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agent.model import configuration_status
from app.api.workspace import Db, require_project
from app.core.database import SessionLocal
from app.models.agent import AgentEvent, AgentRun
from app.schemas.agent import RunInput
from app.services.agent_runs import agent_queue, require_run, run_output
from app.services.agent_stream import stream_progress
from app.services.llm_budget import quota_status
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select

router = APIRouter(prefix="/api/projects/{project_id}/agent")
Status = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]


@router.get("/history")
def history(
    project_id: UUID,
    db: Db,
    q: str = Query(default="", max_length=200),
    status: Status | None = None,
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=20, ge=1, le=50),
):
    require_project(db, project_id)
    if start and end and start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期。")
    conditions = [AgentRun.project_id == project_id]
    if q.strip():
        # Literal substring search: user '%'/'_' are not SQL wildcards.
        conditions.append(
            or_(
                AgentRun.question.icontains(q.strip(), autoescape=True),
                AgentRun.report["title"].astext.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        conditions.append(AgentRun.status == status)
    for day, ending in ((start, False), (end, True)):
        if day:
            try:
                boundary = datetime.combine(
                    day + timedelta(days=int(ending)), time(), ZoneInfo("Asia/Shanghai")
                )
            except (ValueError, OverflowError) as exc:
                raise HTTPException(422, "日期超出可查询范围。") from exc
            conditions.append(
                AgentRun.created_at < boundary if ending else AgentRun.created_at >= boundary
            )
    total = db.scalar(select(func.count()).select_from(AgentRun).where(*conditions))
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if (
                not isinstance(value, dict)
                or set(value) != {"at", "id"}
                or not isinstance(value["at"], str)
                or not isinstance(value["id"], str)
            ):
                raise ValueError
            at, identifier = datetime.fromisoformat(value["at"]), UUID(value["id"])
            if not at.tzinfo:
                raise ValueError
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, "历史分页标识无效，请重新查询。") from exc
        conditions.append(
            or_(AgentRun.created_at < at, and_(AgentRun.created_at == at, AgentRun.id < identifier))
        )
    values = db.scalars(
        select(AgentRun)
        .where(*conditions)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(limit + 1)
    ).all()
    more = len(values) > limit
    values = values[:limit]
    next_cursor = None
    if more:
        last = values[-1]
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps({"at": last.created_at.isoformat(), "id": str(last.id)}).encode()
            )
            .decode()
            .rstrip("=")
        )
    return {
        "items": [
            {**run_output(v, detail=False), "report_title": (v.report or {}).get("title")}
            for v in values
        ],
        "total": total,
        "next_cursor": next_cursor,
    }


@router.get("/runs/{run_id}/stream")
async def stream(
    project_id: UUID,
    run_id: UUID,
    request: Request,
    after: int = Query(default=0, ge=0, le=2_147_483_647),
):
    last = request.headers.get("last-event-id")
    if last is not None:
        if not last.isascii() or not last.isdigit() or len(last) > 10:
            raise HTTPException(422, "实时事件编号无效。")
        after = max(after, int(last))
        if after > 2_147_483_647:
            raise HTTPException(422, "实时事件编号无效。")
    # Validate project ownership before sending a successful streaming response.
    from starlette.concurrency import run_in_threadpool

    def validate():
        with SessionLocal() as session:
            require_project(session, project_id)
            require_run(session, project_id, run_id)

    await run_in_threadpool(validate)
    return StreamingResponse(
        stream_progress(request, project_id, run_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/configuration")
def configuration(project_id: UUID, db: Db):
    require_project(db, project_id)
    return {**configuration_status(), "quota": quota_status(db)}


@router.post("/runs", status_code=202)
def create_run(project_id: UUID, data: RunInput, db: Db, request: Request):
    require_project(db, project_id)
    header = request.headers.get("x-analysis-password", "")
    if len(header) > 1536:
        raise HTTPException(401, "调用密码格式无效，请重新输入。")
    try:
        password = unquote(header, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise HTTPException(401, "调用密码格式无效，请重新输入。") from exc
    return run_output(
        agent_queue.submit(
            db,
            project_id,
            data,
            password=password,
            client_key=request.client.host if request.client else "unknown",
        )
    )


@router.get("/runs")
def list_runs(project_id: UUID, db: Db, limit: int = Query(default=20, ge=1, le=50)):
    require_project(db, project_id)
    runs = db.scalars(
        select(AgentRun)
        .where(AgentRun.project_id == project_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id)
        .limit(limit)
    ).all()
    return [run_output(run, detail=False) for run in runs]


@router.get("/runs/{run_id}")
def get_run(project_id: UUID, run_id: UUID, db: Db):
    require_project(db, project_id)
    return run_output(require_run(db, project_id, run_id))


@router.get("/runs/{run_id}/events")
def events(project_id: UUID, run_id: UUID, db: Db, after: int = Query(default=0, ge=0)):
    require_project(db, project_id)
    require_run(db, project_id, run_id)
    values = db.scalars(
        select(AgentEvent)
        .where(AgentEvent.run_id == run_id, AgentEvent.sequence > after)
        .order_by(AgentEvent.sequence)
        .limit(100)
    ).all()
    return [
        {
            "sequence": value.sequence,
            "kind": value.kind,
            "message": value.message,
            "created_at": value.created_at,
        }
        for value in values
    ]


@router.post("/runs/{run_id}/cancel")
def cancel_run(project_id: UUID, run_id: UUID, db: Db):
    require_project(db, project_id)
    return run_output(agent_queue.cancel(db, project_id, run_id))
