import asyncio
import logging
import queue
import threading
from datetime import datetime, timezone
from time import monotonic
from uuid import UUID

from app.agent import model as models
from app.agent.graph import AgentWorkflow
from app.agent.tools import ToolError, ToolExecutor, capture_snapshot
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import exception_location
from app.models.agent import AgentEvent, AgentRun
from app.models.workspace import Project
from app.services.call_access import authorize_new_run
from app.services.llm_budget import QuotaError, ensure_available, settle_unfinished
from fastapi import HTTPException
from sqlalchemy import func, select

logger = logging.getLogger(__name__)
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class RunCancelled(Exception):
    pass


def add_event(db, run, kind, message):
    sequence = (
        db.scalar(select(func.max(AgentEvent.sequence)).where(AgentEvent.run_id == run.id)) or 0
    ) + 1
    db.add(AgentEvent(run_id=run.id, sequence=sequence, kind=kind, message=message))


def emit_event(run_id, kind, message, changes=None):
    with SessionLocal() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run.status in TERMINAL or run.cancel_requested:
            raise RunCancelled
        for key, value in (changes or {}).items():
            if key not in ("plan", "results", "usage"):
                raise ValueError("unsupported task update")
            setattr(run, key, value)
        add_event(db, run, kind, message)
        db.commit()
    logger.info("event=agent_progress run_id=%s stage=%s", run_id, kind)


def finish_run(run_id, status, error=None, report=None):
    with SessionLocal() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        recovered = settle_unfinished(db, run_id)
        # 提交确认丢失时重试收尾不能反向覆盖已经完成的报告。
        if run.status in TERMINAL:
            if recovered:
                db.commit()
            return
        if run.cancel_requested:
            status, error, report = "cancelled", "任务已取消；已发出的模型请求可能已计费。", None
        run.status, run.error, run.report = status, error, report
        run.finished_at = datetime.now(timezone.utc)
        add_event(
            db,
            run,
            status,
            {
                "succeeded": "报告已完成，请核对证据。",
                "cancelled": "任务已取消。",
                "failed": "任务失败，请查看受控错误提示。",
                "interrupted": "任务已中断，不会自动重发模型请求。",
            }[status],
        )
        db.commit()
    logger.info("event=agent_finished run_id=%s status=%s", run_id, status)


def require_run(db, project_id: UUID, run_id: UUID):
    run = db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.project_id == project_id)
    )
    if not run:
        raise HTTPException(404, "当前项目下不存在该分析任务。")
    return run


def run_output(run, *, detail=True):
    data = {
        key: getattr(run, key)
        for key in (
            "id",
            "project_id",
            "request_id",
            "question",
            "provider",
            "model",
            "status",
            "cancel_requested",
            "created_at",
            "started_at",
            "finished_at",
            "error",
        )
    }
    if detail:
        data.update(
            snapshot=run.snapshot,
            plan=run.plan,
            report=run.report,
            usage=run.usage,
            tool_count=len(run.results),
        )
    return data


class AgentQueue:
    def __init__(self):
        self.queue = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.settling = threading.Event()
        self.active = {}
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.settling.clear()
        self.queue = queue.Queue(maxsize=8)
        with SessionLocal() as db:
            pending = db.scalars(
                select(AgentRun).where(AgentRun.status.in_(["queued", "running"]))
            ).all()
            for run in pending:
                run.status = "cancelled" if run.cancel_requested else "interrupted"
                run.finished_at = datetime.now(timezone.utc)
                run.error = "上次运行被中断，不会自动重发模型请求；请检查已有结果后手动提交。"
                add_event(db, run, run.status, "启动时标记上次未完成任务，不自动恢复模型调用。")
            reservations = settle_unfinished(db)
            db.commit()
            logger.info("event=agent_recovered count=%s", len(pending))
            logger.info("event=model_budget_recovered count=%s", reservations)
        self.thread = threading.Thread(target=self.work, name="agent-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def submit(self, db, project_id, data, *, password=None, client_key="local"):
        with self.lock:
            db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            previous = db.scalar(
                select(AgentRun).where(
                    AgentRun.project_id == project_id,
                    AgentRun.request_id == data.request_id,
                )
            )
            if previous:
                if previous.question != data.question or {
                    item["id"] for item in previous.snapshot["sources"]
                } != {str(value) for value in data.source_ids}:
                    raise HTTPException(409, "相同请求编号已用于另一任务，请刷新确认。")
                return previous
            try:
                config = models.resolve_config()
            except models.ModelError as exc:
                raise HTTPException(503, str(exc)) from exc
            if (
                self.stop.is_set()
                or self.settling.is_set()
                or not self.thread
                or not self.thread.is_alive()
                or self.queue.full()
            ):
                raise HTTPException(503, "分析任务队列暂不可用或已满，请稍后重试。")
            # 已存在的同一提交仅返回原任务；只有新建任务需要新的密码授权。
            authorize_new_run(password, client_key)
            try:
                ensure_available(db)
            except QuotaError as exc:
                raise HTTPException(429, str(exc)) from exc
            run = AgentRun(
                project_id=project_id,
                request_id=data.request_id,
                question=data.question,
                provider=config.provider,
                model=config.model,
                snapshot=capture_snapshot(db, project_id, data.source_ids),
            )
            db.add(run)
            db.flush()
            add_event(db, run, "queued", "任务已排队，数据范围与版本已保存。")
            db.commit()
            self.queue.put_nowait((run.id, config))
            logger.info(
                "event=agent_queued project_id=%s run_id=%s sources=%s",
                project_id,
                run.id,
                len(data.source_ids),
            )
            return run

    def cancel(self, db, project_id, run_id):
        run = db.scalar(
            select(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.project_id == project_id)
            .with_for_update()
        )
        if not run:
            raise HTTPException(404, "当前项目下不存在该分析任务。")
        if run.status not in TERMINAL and not run.cancel_requested:
            run.cancel_requested = True
            add_event(db, run, "cancel_requested", "已请求取消，不会发起新的模型调用。")
            if run.status == "queued":
                run.status = "cancelled"
                run.finished_at = datetime.now(timezone.utc)
            db.commit()
        with self.lock:
            signal = self.active.get(run_id)
            if signal:
                signal.set()
        return run

    async def execute(self, run, config, signal):
        model = models.create_model(config)
        model.run_id = run.id
        executor = ToolExecutor(run.project_id, run.snapshot)

        async def emit(kind, message, **changes):
            if signal.is_set() or self.stop.is_set():
                raise RunCancelled
            await asyncio.to_thread(emit_event, run.id, kind, message, changes)

        workflow = AgentWorkflow(
            run,
            model,
            executor,
            emit,
            deadline=monotonic() + get_settings().agent_timeout_seconds,
        )

        async def monitor():
            while not signal.is_set() and not self.stop.is_set():
                await asyncio.sleep(0.1)
            raise RunCancelled

        task = asyncio.create_task(workflow.run_graph())
        cancel = asyncio.create_task(monitor())
        try:
            async with asyncio.timeout(get_settings().agent_timeout_seconds):
                done, _ = await asyncio.wait([task, cancel], return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
                return task.result()["report"]
        finally:
            for pending in (task, cancel):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, cancel, return_exceptions=True)
            # 连接收尾也有期限；不能让无限等待绕过任务总期限。
            try:
                async with asyncio.timeout(5):
                    await model.close()
            except TimeoutError:
                logger.error("event=model_close_timeout run_id=%s", run.id)

    def work(self):
        while not self.stop.is_set():
            try:
                run_id, config = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            started = monotonic()
            status, error, report = "failed", "分析任务处理失败，请检查服务日志中的任务编号。", None
            signal = threading.Event()
            try:
                with SessionLocal() as db:
                    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
                    if run.status != "queued":
                        continue
                    run.status = "running"
                    run.started_at = datetime.now(timezone.utc)
                    add_event(db, run, "running", "任务开始执行。")
                    db.commit()
                with self.lock:
                    self.active[run_id] = signal
                report = asyncio.run(self.execute(run, config, signal))
                status, error = "succeeded", None
            except RunCancelled:
                status = "interrupted" if self.stop.is_set() else "cancelled"
                error = "任务被中断；已发出的模型请求可能已计费，不会自动恢复。"
            except TimeoutError:
                error = "任务超过总执行期限；不会自动重发可能已计费的模型请求。"
            except (models.ModelError, ToolError) as exc:
                error = str(exc)
                logger.warning(
                    "event=agent_controlled_failure run_id=%s type=%s", run_id, type(exc).__name__
                )
            except Exception as exc:
                logger.error(
                    "event=agent_failure run_id=%s type=%s location=%s",
                    run_id,
                    type(exc).__name__,
                    exception_location(exc),
                )
            finally:
                with self.lock:
                    self.active.pop(run_id, None)
                # 仅重试状态持久化，不重跑模型或工具；故障期间暂停领取和新提交。
                attempt = 0
                while True:
                    try:
                        finish_run(run_id, status, error, report)
                        self.settling.clear()
                        break
                    except Exception as exc:
                        self.settling.set()
                        logger.error(
                            "event=agent_finalize_retry run_id=%s type=%s location=%s",
                            run_id,
                            type(exc).__name__,
                            exception_location(exc),
                        )
                        attempt += 1
                        if self.stop.wait(min(2 ** min(attempt, 3), 5)):
                            break
                self.queue.task_done()
                logger.info(
                    "event=agent_worker_done run_id=%s duration_ms=%.1f",
                    run_id,
                    (monotonic() - started) * 1000,
                )


agent_queue = AgentQueue()
