import logging
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.database import get_db
from app.ingestion.parsers import ParseError, csv_preview, extract_pdf_text, read_pdf
from app.models.workspace import DataSource, Project
from app.schemas.workspace import ProjectInput, ProjectOutput, SourceOutput
from app.services.analysis import file_hash
from app.services.sources import process_source, source_path
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
Db = Annotated[Session, Depends(get_db)]


def require_project(db: Session, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在。")
    return project


def require_source(
    db: Session, project_id: UUID, source_id: UUID, *, lock: bool = False
) -> DataSource:
    require_project(db, project_id)
    query = select(DataSource).where(
        DataSource.id == source_id, DataSource.project_id == project_id
    )
    source = db.scalar(query.with_for_update() if lock else query)
    if not source:
        raise HTTPException(404, "当前项目下不存在该数据源。")
    return source


def verify_original(path: Path, expected_hash: str | None):
    # 历史索引/报告只能定位其当时的文件，不能将旧记录序号套到已变化的原文。
    if expected_hash and file_hash(path) != expected_hash:
        raise HTTPException(409, "原文件已变化，无法按历史证据定位；请重新分析或重新检索。")


@router.get("/projects", response_model=list[ProjectOutput])
def list_projects(db: Db):
    return db.scalars(select(Project).order_by(Project.updated_at.desc())).all()


@router.post("/projects", response_model=ProjectOutput, status_code=201)
def create_project(data: ProjectInput, db: Db):
    project = Project(**data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    logger.info("event=project_created project_id=%s", project.id)
    return project


@router.get("/projects/{project_id}", response_model=ProjectOutput)
def get_project(project_id: UUID, db: Db):
    return require_project(db, project_id)


@router.put("/projects/{project_id}", response_model=ProjectOutput)
def update_project(project_id: UUID, data: ProjectInput, db: Db):
    project = require_project(db, project_id)
    project.name = data.name
    project.description = data.description
    db.commit()
    db.refresh(project)
    logger.info("event=project_updated project_id=%s", project.id)
    return project


@router.get("/projects/{project_id}/sources", response_model=list[SourceOutput])
def list_sources(project_id: UUID, db: Db):
    require_project(db, project_id)
    return db.scalars(
        select(DataSource)
        .where(DataSource.project_id == project_id)
        .order_by(DataSource.created_at.desc())
    ).all()


@router.post("/projects/{project_id}/sources", response_model=SourceOutput, status_code=201)
def upload_source(project_id: UUID, db: Db, file: Annotated[UploadFile, File()]):
    require_project(db, project_id)
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(filename).suffix.lower()
    if (
        not filename
        or len(filename) > 255
        or suffix not in {".csv", ".pdf"}
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise HTTPException(415, "只支持文件名不超过 255 字符的 CSV 或 PDF。")
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    key = f"{uuid4().hex}{suffix}"
    path = settings.upload_dir / key
    size = 0
    try:
        with path.open("xb") as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, f"文件不能超过 {settings.max_upload_mb} MB。")
                target.write(chunk)
        if not size:
            raise HTTPException(422, "文件为空。")
        source = DataSource(
            project_id=project_id,
            filename=filename,
            storage_key=key,
            kind=suffix[1:],
            size_bytes=size,
        )
        db.add(source)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        db.rollback()
        raise
    finally:
        file.file.close()
    return process_source(db, source)


@router.post("/projects/{project_id}/sources/{source_id}/retry", response_model=SourceOutput)
def retry_source(project_id: UUID, source_id: UUID, db: Db):
    # Lock the status check until process_source commits its processing claim.
    source = require_source(db, project_id, source_id, lock=True)
    if source.status != "failed":
        raise HTTPException(409, "仅失败的数据源支持重试。")
    return process_source(db, source)


@router.get("/projects/{project_id}/sources/{source_id}/preview")
def preview_source(
    project_id: UUID,
    source_id: UUID,
    db: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    expected_hash: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
):
    source = require_source(db, project_id, source_id)
    if source.status != "ready":
        raise HTTPException(409, "数据源尚未就绪，无法预览。")
    try:
        path = source_path(source)
        verify_original(path, expected_hash)
        if source.kind == "csv":
            result = {"kind": "csv", **csv_preview(path, page, page_size)}
        else:
            reader = read_pdf(path)
            if page > len(reader.pages):
                raise HTTPException(404, "PDF 页码超出范围。")
            content = extract_pdf_text(reader.pages[page - 1])
            result = {
                "kind": "pdf",
                "page": page,
                "page_count": len(reader.pages),
                "text": content[:100_000],
                "text_truncated": len(content) > 100_000,
            }
        verify_original(path, expected_hash)
        return result
    except (ParseError, OSError) as exc:
        raise HTTPException(409, "原文件不可用，请检查文件或重新上传。") from exc


@router.get("/projects/{project_id}/sources/{source_id}/download")
def download_source(
    project_id: UUID,
    source_id: UUID,
    db: Db,
    expected_hash: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
):
    source = require_source(db, project_id, source_id)
    path = source_path(source)
    if not path.is_file():
        raise HTTPException(404, "原文件不存在。")
    verify_original(path, expected_hash)
    return FileResponse(path, filename=source.filename, media_type="application/octet-stream")
