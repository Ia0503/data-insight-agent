import logging
from pathlib import Path
from time import perf_counter

from app.core.config import get_settings
from app.core.logging import exception_location
from app.ingestion.parsers import ParseError, inspect_file
from app.models.workspace import DataSource
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def source_path(source: DataSource) -> Path:
    # storage_key is server-generated, never derived from the uploaded filename.
    return get_settings().upload_dir / source.storage_key


def process_source(db: Session, source: DataSource) -> DataSource:
    started = perf_counter()
    source.status = "processing"
    source.error = None
    source.metadata_json = {}
    # 先持久化处理中状态；崩溃时启动恢复可将它标记为失败，而非留下无法重试的记录。
    db.commit()
    logger.info(
        "event=ingestion_started project_id=%s source_id=%s kind=%s size_bytes=%s",
        source.project_id,
        source.id,
        source.kind,
        source.size_bytes,
    )
    try:
        source.metadata_json = inspect_file(source_path(source), source.kind)
        source.status = "ready"
    except ParseError as exc:
        source.status = "failed"
        source.error = str(exc)
    except Exception as exc:
        logger.error(
            "event=ingestion_exception source_id=%s exception_type=%s location=%s",
            source.id,
            type(exc).__name__,
            exception_location(exc),
        )
        source.status = "failed"
        source.error = "文件处理失败，可重试；若仍失败请检查文件或服务日志。"
    db.commit()
    db.refresh(source)
    logger.log(
        logging.INFO if source.status == "ready" else logging.WARNING,
        "event=ingestion_finished project_id=%s source_id=%s status=%s duration_ms=%.1f",
        source.project_id,
        source.id,
        source.status,
        (perf_counter() - started) * 1000,
    )
    return source
