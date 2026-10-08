import logging
import queue
import threading
import time
from uuid import UUID

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import exception_location
from app.ingestion.parsers import extract_pdf_text, read_pdf
from app.models.analysis import DocumentChunk, DocumentIndex
from app.models.workspace import DataSource
from app.services import embeddings
from app.services.analysis import dataset, file_hash
from app.services.sources import source_path
from fastapi import HTTPException
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


def profile():
    value = embeddings.provider.profile()
    return {**value, "key": embeddings.fingerprint(value)}


def index_output(index):
    return {
        "id": str(index.id),
        "source_id": str(index.source_id),
        "status": index.status,
        "active": index.active,
        "chunk_count": index.chunk_count,
        "error": index.error,
        "profile": index.profile,
        "options": index.options,
        "content_hash": index.content_hash,
        "created_at": index.created_at,
    }


def list_indexes(db, project_id, source_id=None):
    query = select(DocumentIndex).where(DocumentIndex.project_id == project_id)
    if source_id:
        query = query.where(DocumentIndex.source_id == source_id)
    return db.scalars(query.order_by(DocumentIndex.created_at.desc())).all()


def activate(db, index):
    if index.status != "ready" or index.profile != profile():
        raise HTTPException(
            409, "仅能启用与当前模型配置一致的就绪索引；回退模型时请同步配置并重启。"
        )
    source = db.scalar(select(DataSource).where(DataSource.id == index.source_id).with_for_update())
    if file_hash(source_path(source)) != index.content_hash:
        raise HTTPException(409, "原文件已变化，请重新建立索引。")
    # 同一事务切换 active 标记；旧版本和证据保持完整，失败不影响当前索引。
    db.execute(
        update(DocumentIndex)
        .where(DocumentIndex.source_id == index.source_id, DocumentIndex.active)
        .values(active=False)
    )
    db.flush()
    index.active = True
    db.commit()
    logger.info(
        "event=index_activated project_id=%s source_id=%s index_id=%s",
        index.project_id,
        index.source_id,
        index.id,
    )
    return index


def source_parts(source, options):
    total = 0
    if source.kind == "pdf":
        reader = read_pdf(source_path(source))
        if len(reader.pages) > 300:
            raise ValueError("索引最多支持 300 页 PDF，请拆分文件。")
        for page, content in enumerate(reader.pages, 1):
            text = extract_pdf_text(content)
            total += len(text)
            if len(text) > 100_000 or total > 2_000_000:
                raise ValueError("文档文本超过索引限制，请拆分文件。")
            if text.strip():
                yield {"page": page, "record": None, "record_id": None}, text
    else:
        frame, _ = dataset(source)
        field, id_field = options["text_field"], options["record_id_field"]
        if field not in frame or (id_field and id_field not in frame):
            raise ValueError("所选文本列或编号列不存在。")
        for record, row in enumerate(frame.to_dict("records"), 1):
            text = row[field]
            total += len(text)
            if len(text) > 100_000 or total > 2_000_000:
                raise ValueError("反馈文本超过索引限制，请拆分文件。")
            if text.strip():
                yield (
                    {
                        "page": None,
                        "record": record,
                        "record_id": row[id_field] if id_field else None,
                    },
                    text,
                )


class IndexTaskError(Exception):
    """Only an application-controlled message crosses the queue recovery boundary."""


def fail_index(index_id, message):
    # 新事务收尾：领取失败可能仍为 queued，提交响应丢失时也可能已经 ready。
    # 只改变待处理状态，不覆盖已提交的就绪索引或旧 active 版本。
    with SessionLocal() as db:
        db.execute(
            update(DocumentIndex)
            .where(DocumentIndex.id == index_id, DocumentIndex.status.in_(["queued", "processing"]))
            .values(status="failed", active=False, error=message)
        )
        db.commit()


def process_index(index_id, stop):
    started = time.monotonic()
    try:
        with SessionLocal() as db:
            index = db.get(DocumentIndex, index_id)
            if not index or index.status != "queued":
                return
            index.status = "processing"
            db.commit()
            logger.info(
                "event=index_started project_id=%s source_id=%s index_id=%s",
                index.project_id,
                index.source_id,
                index.id,
            )
            if index.profile != profile():
                raise ValueError("模型配置发生变化，请重新提交索引。")
            source = db.get(DataSource, index.source_id)
            chunks = []
            for locator, content in source_parts(source, index.options):
                if stop.is_set() or time.monotonic() - started > 600:
                    raise ValueError("索引任务中断或超过十分钟，请重试或拆分文档。")
                for chunk in embeddings.provider.chunks(content):
                    chunks.append({**locator, **chunk})
                    if len(chunks) > 2000:
                        raise ValueError("分块超过 2000，请拆分文档。")
            if not chunks:
                raise ValueError("没有可索引文本，请检查文件。")
            for offset in range(0, len(chunks), 8):
                if stop.is_set() or time.monotonic() - started > 600:
                    raise ValueError("索引任务中断或超过十分钟，请重试或拆分文档。")
                batch = chunks[offset : offset + 8]
                vectors = embeddings.provider.encode([chunk["content"] for chunk in batch])
                embeddings.validate_vectors(vectors, len(batch), index.profile["dimension"])
                for ordinal, (chunk, vector) in enumerate(
                    zip(batch, vectors, strict=True), offset + 1
                ):
                    db.add(
                        DocumentChunk(index_id=index.id, ordinal=ordinal, embedding=vector, **chunk)
                    )
            if file_hash(source_path(source)) != index.content_hash:
                raise ValueError("任务期间原文件变化，请重新索引。")
            index.chunk_count = len(chunks)
            index.status = "ready"
            db.flush()
            activate(db, index)
            logger.info(
                "event=index_finished index_id=%s status=ready chunks=%s duration_ms=%.1f",
                index_id,
                len(chunks),
                (time.monotonic() - started) * 1000,
            )
    except Exception as exc:
        logger.warning(
            "event=index_processing_error index_id=%s exception_type=%s location=%s duration_ms=%.1f",
            index_id,
            type(exc).__name__,
            exception_location(exc),
            (time.monotonic() - started) * 1000,
        )
        message = (
            str(exc)
            if isinstance(exc, (ValueError, embeddings.EmbeddingError))
            else "索引处理失败，请检查文件、模型和数据库后重试。"
        )
        raise IndexTaskError(message) from exc


class IndexQueue:
    def __init__(self):
        self.queue = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.recovering = False

    def start(self):
        self.stop_event.clear()
        self.recovering = False
        with SessionLocal() as db:
            count = (
                db.query(DocumentIndex)
                .filter(DocumentIndex.status.in_(["queued", "processing"]))
                .update(
                    {"status": "failed", "error": "上次索引任务中断，请重试。", "active": False}
                )
            )
            db.commit()
        logger.info("event=index_recovered count=%s", count)
        self.thread = threading.Thread(target=self._run, name="newai-index", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                index_id = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                process_index(index_id, self.stop_event)
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, IndexTaskError)
                    else "索引任务异常中断，请检查数据库后重试。"
                )
                with self.lock:
                    self.recovering = True
                # 仅保留一个待收尾任务，暂停领取后续任务；不会重跑推理或无限增长重试队列。
                # 数据库恢复后自动写入 failed，等待期间拒绝新提交，关闭可中断退避等待。
                attempt = 0
                while not self.stop_event.is_set():
                    try:
                        fail_index(index_id, message)
                        logger.info("event=index_failure_settled index_id=%s", index_id)
                        break
                    except Exception as recovery_exc:
                        attempt += 1
                        logger.warning(
                            "event=index_recovery_pending index_id=%s attempt=%s exception_type=%s location=%s",
                            index_id,
                            attempt,
                            type(recovery_exc).__name__,
                            exception_location(recovery_exc),
                        )
                        self.stop_event.wait(min(2 ** min(attempt - 1, 3), 5))
                with self.lock:
                    self.recovering = False
            finally:
                self.queue.task_done()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        embeddings.provider.close()
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break

    def submit(self, db, source, data):
        options = data.model_dump(exclude={"force"})
        if source.kind == "csv":
            frame, _ = dataset(source)
            if options["text_field"] not in frame or (
                options["record_id_field"] and options["record_id_field"] not in frame
            ):
                raise HTTPException(422, "反馈 CSV 请明确选择文本列和可选编号列。")
        elif options["text_field"] or options["record_id_field"]:
            raise HTTPException(422, "PDF 不接受 CSV 字段配置。")
        current = profile()
        digest = file_hash(source_path(source))
        s = get_settings()
        options.update({"chunk_tokens": s.chunk_tokens, "overlap": s.chunk_overlap})
        signature = embeddings.fingerprint(
            {"profile": current, "options": options, "content_hash": digest}
        )
        with self.lock:
            indexes = list_indexes(db, source.project_id, source.id)
            running = next((i for i in indexes if i.status in {"queued", "processing"}), None)
            if running:
                raise HTTPException(409, "该文件已有索引任务，请等待完成。")
            ready = next(
                (i for i in indexes if i.signature == signature and i.status == "ready"), None
            )
            if ready and not data.force:
                return activate(db, ready)
            if len(indexes) >= 20:
                raise HTTPException(409, "该文件已保存 20 个索引版本，请先规划历史版本清理。")
            if (
                self.recovering
                or not self.thread
                or not self.thread.is_alive()
                or self.queue.full()
            ):
                raise HTTPException(503, "索引队列不可用或已满，请稍后重试。")
            index = DocumentIndex(
                project_id=source.project_id,
                source_id=source.id,
                signature=signature,
                content_hash=digest,
                profile=current,
                options=options,
            )
            db.add(index)
            db.commit()
            self.queue.put_nowait(index.id)
            logger.info(
                "event=index_queued project_id=%s source_id=%s index_id=%s",
                source.project_id,
                source.id,
                index.id,
            )
            return index


index_queue = IndexQueue()


def search(db, project_id: UUID, data, *, index_ids=None):
    started = time.monotonic()
    indexes = [
        i
        for i in list_indexes(db, project_id)
        if (i.active if index_ids is None else i.id in index_ids)
        and i.status == "ready"
        and (not data.source_ids or i.source_id in data.source_ids)
    ]
    if len(indexes) > 100:
        raise HTTPException(422, "一次检索最多包含 100 个文件，请明确缩小检索范围。")
    if not indexes:
        return {"results": [], "reason": "所选文件尚无已启用索引，请先建立索引。"}
    if any(i.profile != profile() for i in indexes):
        raise HTTPException(409, "已启用索引与当前模型不一致，请重建索引或恢复匹配的模型配置。")
    for index in indexes:
        if file_hash(source_path(db.get(DataSource, index.source_id))) != index.content_hash:
            raise HTTPException(409, "索引对应的原文件已变化，请重建索引。")
    try:
        vectors = embeddings.provider.encode([data.query], query=True)
        embeddings.validate_vectors(vectors, 1, profile()["dimension"])
    except embeddings.EmbeddingError as exc:
        raise HTTPException(503, str(exc)) from exc
    # MATERIALIZED 确保其他项目/其他维度的向量在距离计算前已被过滤。
    candidates = (
        select(DocumentChunk.id, DocumentChunk.embedding)
        .where(DocumentChunk.index_id.in_([i.id for i in indexes]))
        .cte("candidates")
        .prefix_with("MATERIALIZED")
    )
    distance = candidates.c.embedding.cosine_distance(vectors[0]).label("distance")
    matches = db.execute(
        select(candidates.c.id, distance).order_by(distance, candidates.c.id).limit(data.top_k)
    ).all()
    results = []
    for chunk_id, dist in matches:
        similarity = max(-1.0, min(1.0, 1 - float(dist)))
        if similarity < data.min_similarity:
            continue
        chunk = db.get(DocumentChunk, chunk_id)
        index = db.get(DocumentIndex, chunk.index_id)
        source = db.get(DataSource, index.source_id)
        results.append(
            {
                "chunk_id": str(chunk.id),
                "index_id": str(index.id),
                "source_id": str(source.id),
                "filename": source.filename,
                "kind": source.kind,
                "page": chunk.page,
                "record": chunk.record,
                "record_id": chunk.record_id,
                "start": chunk.start,
                "end": chunk.end,
                "text": chunk.content,
                "similarity": round(similarity, 6),
                "content_hash": index.content_hash,
                "model": index.profile["model"],
                "revision": index.profile["revision"],
            }
        )
    logger.info(
        "event=search_finished project_id=%s results=%s duration_ms=%.1f",
        project_id,
        len(results),
        (time.monotonic() - started) * 1000,
    )
    return {
        "results": results,
        "reason": None if results else "没有达到当前相似度阈值的结果。",
        "notice": "相似度是检索排序分数，不代表正确概率；请核对引用原文。",
    }
