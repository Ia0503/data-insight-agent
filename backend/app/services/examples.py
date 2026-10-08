"""Fixed fictional assets; no caller-controlled path or automatic generation-model call."""

import logging
from uuid import NAMESPACE_URL, uuid5

from app.core.config import ROOT, get_settings
from app.models.analysis import DatasetMapping, DocumentIndex
from app.models.workspace import DataSource, Project
from app.schemas.analysis import IndexInput
from app.services.analysis import FIELDS, file_hash, save_mapping
from app.services.retrieval import index_queue
from app.services.sources import process_source, source_path
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)
NAME = "业务分析示例(test)"
DESCRIPTION = (
    "人工构造的订单、销售汇总、用户反馈和中文 PDF。汇总与明细不能重复计算；仅用于功能检查。"
)
PROJECT_ID = uuid5(NAMESPACE_URL, "newai:fictional-business-v1")
MARKER = "business-v1"
ASSETS = (
    "sales_summary.csv",
    "feedback.csv",
    "product_manual.pdf",
    "quarterly_report.pdf",
    "orders.csv",
)
DATA = ROOT / "test" / "data" / "business"


def existing_project(db):
    fixed = db.get(Project, PROJECT_ID)
    if fixed:
        return fixed
    tagged = db.scalar(
        select(Project)
        .join(DataSource)
        .where(DataSource.metadata_json["builtin_example"].astext == MARKER)
        .order_by(Project.created_at)
        .limit(1)
    )
    if tagged:
        return tagged
    # Adopt the earlier explicit seed project, retaining all files, mappings and indexes.
    return db.scalar(
        select(Project)
        .where(
            Project.name.in_((NAME, "业务分析示例（虚构数据）")), Project.description == DESCRIPTION
        )
        .order_by(Project.created_at)
        .limit(1)
    )


def prepare(db):
    value = existing_project(db)
    if value:
        if value.name == "业务分析示例（虚构数据）":
            value.name = NAME
            db.commit()
        return value
    # A deterministic PK and ON CONFLICT keep concurrent/retried imports idempotent.
    db.execute(
        insert(Project)
        .values(id=PROJECT_ID, name=NAME, description=DESCRIPTION)
        .on_conflict_do_nothing(index_elements=[Project.id])
    )
    db.commit()
    logger.info("event=example_project_prepared project_id=%s", PROJECT_ID)
    return db.get(Project, PROJECT_ID)


def asset_source(db, project_id, filename):
    return db.scalar(
        select(DataSource)
        .where(DataSource.project_id == project_id, DataSource.filename == filename)
        .order_by(DataSource.created_at)
        .limit(1)
    )


def import_asset(db, filename):
    if filename not in ASSETS:
        raise HTTPException(404, "不存在该内置示例文件。")
    path = DATA / filename
    if not path.is_file():
        raise HTTPException(503, "内置示例文件未准备好，请检查 test/data/business。")
    project = prepare(db)
    db.scalar(select(Project).where(Project.id == project.id).with_for_update())
    source = asset_source(db, project.id, filename)
    if source:
        try:
            if file_hash(source_path(source)) != file_hash(path):
                raise HTTPException(409, "已有示例原文件已变化，保留当前内容；请在项目详情检查。")
        except OSError as exc:
            raise HTTPException(409, "已有示例原文件不可用，未覆盖；请在项目详情检查。") from exc
        if source.status == "failed":
            source = process_source(db, source)
    else:
        identifier = uuid5(project.id, filename)
        key = f"{identifier.hex}{path.suffix}"
        settings = get_settings()
        target = settings.upload_dir / key
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Only this fixed asset can claim the deterministic storage slot. An orphan
            # from a pre-commit interruption is reusable if its bytes exactly match.
            if target.exists():
                if file_hash(target) != file_hash(path):
                    raise HTTPException(409, "示例文件存储位置已被占用，未覆盖。")
            else:
                with target.open("xb") as output:
                    output.write(path.read_bytes())
            source = DataSource(
                id=identifier,
                project_id=project.id,
                filename=filename,
                storage_key=key,
                kind=path.suffix[1:],
                size_bytes=path.stat().st_size,
                status="processing",
            )
            db.add(source)
            db.commit()
            source = process_source(db, source)
        except OSError as exc:
            db.rollback()
            raise HTTPException(503, "示例文件写入失败，可稍后补齐。") from exc
    warning = None
    if source.status == "ready":
        # The legacy seeded project has a random ID; mark its assets so later
        # user edits to name/description do not silently create a second example.
        source.metadata_json = {**source.metadata_json, "builtin_example": MARKER}
        if filename == "orders.csv" and not db.get(DatasetMapping, source.id):
            save_mapping(db, source, {field: field for field in FIELDS})
        elif filename != "sales_summary.csv" and filename != "orders.csv":
            db.scalar(select(DataSource).where(DataSource.id == source.id).with_for_update())
            indexes = db.scalars(
                select(DocumentIndex).where(
                    DocumentIndex.source_id == source.id,
                    (DocumentIndex.active | DocumentIndex.status.in_(("queued", "processing"))),
                )
            ).all()
            if not indexes:
                if not (get_settings().embedding_dir / "manifest.json").is_file():
                    warning = "文件已导入，向量模型尚未准备好；准备后可再次补齐索引。"
                else:
                    try:
                        options = (
                            IndexInput(text_field="text", record_id_field="feedback_id")
                            if filename == "feedback.csv"
                            else IndexInput()
                        )
                        index_queue.submit(db, source, options)
                    except HTTPException:
                        db.rollback()
                        warning = "文件已导入，索引暂未提交；可再次补齐或到分析与检索查看。"
    db.commit()
    logger.info(
        "event=example_asset_ready project_id=%s source_id=%s status=%s",
        project.id,
        source.id,
        source.status,
    )
    return source, warning


def status(db):
    project = existing_project(db)
    entries = []
    for filename in ASSETS:
        source = asset_source(db, project.id, filename) if project else None
        indexes = (
            db.scalars(
                select(DocumentIndex)
                .where(DocumentIndex.source_id == source.id)
                .order_by(DocumentIndex.created_at.desc())
            ).all()
            if source
            else []
        )
        index = next((v for v in indexes if v.active), indexes[0] if indexes else None)
        entries.append(
            {
                "filename": filename,
                "source_id": source.id if source else None,
                "status": source.status if source else "missing",
                "error": source.error if source else None,
                "index_status": index.status if index else None,
                "index_error": index.error if index else None,
            }
        )
    return {
        "project": project,
        "files": entries,
        "model_downloaded": (get_settings().embedding_dir / "manifest.json").is_file(),
    }
