import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.api.agent import router as agent_router
from app.api.analysis import router as analysis_router
from app.api.examples import router as examples_router
from app.api.workspace import router
from app.core.config import get_settings
from app.core.database import SessionLocal, acquire_instance, engine, release_instance
from app.core.http import RequestBoundaryMiddleware
from app.core.logging import configure_logging
from app.models.workspace import DataSource

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().upload_dir.mkdir(parents=True, exist_ok=True)
    # An interrupted direct ingestion cannot resume automatically; make retry available.
    from starlette.concurrency import run_in_threadpool

    def recover():
        with SessionLocal() as db:
            count = (
                db.query(DataSource)
                .filter(DataSource.status == "processing")
                .update({"status": "failed", "error": "上次处理被中断，请重试。"})
            )
            db.commit()
            logger.info("event=ingestion_recovered count=%s", count)

    from app.services.agent_runs import agent_queue
    from app.services.retrieval import index_queue

    lease = await run_in_threadpool(acquire_instance)
    index_started = agent_started = False
    try:
        await run_in_threadpool(recover)
        await run_in_threadpool(index_queue.start)
        index_started = True
        await run_in_threadpool(agent_queue.start)
        agent_started = True
        yield
    finally:
        if agent_started:
            await run_in_threadpool(agent_queue.close)
        if index_started:
            await run_in_threadpool(index_queue.close)
        await run_in_threadpool(release_instance, lease)
        engine.dispose()


app = FastAPI(title="AI Business Analyst", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(analysis_router)
app.include_router(agent_router)
app.include_router(examples_router)
app.add_middleware(RequestBoundaryMiddleware)


@app.get("/api/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            version = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
        if not version:
            raise RuntimeError("pgvector extension missing")
        return {
            "status": "ok",
            "database": "connected",
            "pgvector": version,
            "max_upload_mb": get_settings().max_upload_mb,
        }
    except Exception as exc:
        logger.warning("Database health check failed: %s", type(exc).__name__)
        raise HTTPException(
            503, "数据库不可用或 pgvector 未初始化，请检查配置并运行迁移。"
        ) from exc
