"""Explicit read-only check of the local development workspace before/after restart."""

import argparse
import json

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.agent import AgentRun
from app.models.analysis import DatasetMapping, DocumentIndex
from app.models.workspace import DataSource, Project
from app.services.analysis import file_hash
from app.services.sources import source_path
from sqlalchemy import select
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-enabled", action="store_true", help="仅允许读取已开启调用的开发库；不会请求模型"
    )
    args = parser.parse_args()
    settings = get_settings()
    if make_url(settings.database_url.get_secret_value()).database != "newai":
        raise RuntimeError("This manual check only accepts the local newai development database.")
    if settings.llm_calls_enabled and not args.allow_enabled:
        raise RuntimeError("Expected disabled generation-model calls during read-only validation.")
    with SessionLocal() as db:
        projects = db.scalars(select(Project).order_by(Project.id)).all()
        sources = db.scalars(select(DataSource).order_by(DataSource.id)).all()
        indexes = db.scalars(select(DocumentIndex).order_by(DocumentIndex.id)).all()
        runs = db.scalars(select(AgentRun).order_by(AgentRun.id)).all()
        mappings = db.scalars(select(DatasetMapping).order_by(DatasetMapping.source_id)).all()
        if (
            any(s.status == "processing" for s in sources)
            or any(i.status in {"queued", "processing"} for i in indexes)
            or any(r.status in {"queued", "running"} for r in runs)
        ):
            raise RuntimeError("Development tasks are active; do not restart the service yet.")
        result = {
            "projects": [{"id": str(p.id), "name": p.name} for p in projects],
            "sources": [
                {"id": str(s.id), "status": s.status, "hash": file_hash(source_path(s))}
                for s in sources
            ],
            "indexes": [{"id": str(i.id), "status": i.status, "active": i.active} for i in indexes],
            "runs": [{"id": str(r.id), "status": r.status} for r in runs],
            "mappings": [
                {"source_id": str(m.source_id), "version": str(m.version)} for m in mappings
            ],
            "model_enabled": settings.llm_calls_enabled,
        }
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
