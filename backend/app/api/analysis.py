from uuid import UUID

from app.api.workspace import Db, require_project, require_source
from app.models.analysis import DatasetMapping, DocumentChunk, DocumentIndex
from app.schemas.analysis import IndexInput, MappingInput, MetricInput, SearchInput, ToolInput
from app.services.analysis import calculate_metric, controlled_tool, save_mapping
from app.services.retrieval import (
    activate,
    index_output,
    index_queue,
    list_indexes,
    profile,
    search,
)
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects/{project_id}")


@router.get("/analysis/model")
def model(project_id: UUID, db: Db):
    require_project(db, project_id)
    from app.core.config import get_settings

    return {
        "profile": profile(),
        "downloaded": (get_settings().embedding_dir / "manifest.json").is_file(),
    }


@router.get("/sources/{source_id}/mapping")
def get_mapping(project_id: UUID, source_id: UUID, db: Db):
    require_source(db, project_id, source_id)
    value = db.get(DatasetMapping, source_id)
    return {
        "fields": value.fields if value else {},
        "version": str(value.version) if value else None,
    }


@router.put("/sources/{source_id}/mapping")
def put_mapping(project_id: UUID, source_id: UUID, data: MappingInput, db: Db):
    return save_mapping(db, require_source(db, project_id, source_id), data.fields)


@router.post("/sources/{source_id}/tools")
def tools(project_id: UUID, source_id: UUID, data: ToolInput, db: Db):
    return controlled_tool(require_source(db, project_id, source_id), data)


@router.post("/sources/{source_id}/metrics")
def metrics(project_id: UUID, source_id: UUID, data: MetricInput, db: Db):
    return calculate_metric(db, require_source(db, project_id, source_id), data)


@router.get("/indexes")
def indexes(project_id: UUID, db: Db):
    require_project(db, project_id)
    return [index_output(value) for value in list_indexes(db, project_id)]


@router.post("/sources/{source_id}/indexes", status_code=202)
def create_index(project_id: UUID, source_id: UUID, data: IndexInput, db: Db):
    source = require_source(db, project_id, source_id, lock=True)
    if source.status != "ready":
        raise HTTPException(409, "请先完成文件解析。")
    return index_output(index_queue.submit(db, source, data))


@router.post("/indexes/{index_id}/activate")
def activate_index(project_id: UUID, index_id: UUID, db: Db):
    require_project(db, project_id)
    value = db.get(DocumentIndex, index_id)
    if not value or value.project_id != project_id:
        raise HTTPException(404, "当前项目下不存在该索引。")
    return index_output(activate(db, value))


@router.get("/citations/{chunk_id}")
def citation(project_id: UUID, chunk_id: UUID, db: Db):
    require_project(db, project_id)
    chunk = db.get(DocumentChunk, chunk_id)
    index = db.get(DocumentIndex, chunk.index_id) if chunk else None
    if not index or index.project_id != project_id or index.status != "ready":
        raise HTTPException(404, "当前项目下不存在该引用。")
    source = require_source(db, project_id, index.source_id)
    return {
        "chunk_id": str(chunk.id),
        "index_id": str(index.id),
        "filename": source.filename,
        "source_id": str(source.id),
        "page": chunk.page,
        "record": chunk.record,
        "record_id": chunk.record_id,
        "start": chunk.start,
        "end": chunk.end,
        "text": chunk.content,
        "content_hash": index.content_hash,
        "historical": not index.active,
    }


@router.post("/search")
def retrieve(project_id: UUID, data: SearchInput, db: Db):
    require_project(db, project_id)
    for source_id in data.source_ids:
        require_source(db, project_id, source_id)
    return search(db, project_id, data)
