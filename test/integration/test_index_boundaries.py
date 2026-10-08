import time
from uuid import uuid4

from app.core.database import SessionLocal
from app.models.analysis import DocumentIndex
from app.models.workspace import DataSource, Project
from app.services import embeddings
from app.services.retrieval import index_queue
from sqlalchemy import select


def test_recovery_preserves_existing_active_index(migrate_test_database):
    from app.main import app
    from fastapi.testclient import TestClient

    with SessionLocal() as db:
        project = Project(name="Index recovery boundary(test)")
        db.add(project)
        db.flush()
        source = DataSource(
            project_id=project.id,
            filename="recover.pdf",
            storage_key=f"{uuid4()}.pdf",
            kind="pdf",
            size_bytes=1,
            status="ready",
        )
        db.add(source)
        db.flush()
        active = DocumentIndex(
            project_id=project.id,
            source_id=source.id,
            signature="old",
            content_hash="old",
            profile={},
            options={},
            status="ready",
            active=True,
        )
        pending = DocumentIndex(
            project_id=project.id,
            source_id=source.id,
            signature="new",
            content_hash="new",
            profile={},
            options={},
            status="processing",
            active=False,
        )
        db.add_all([active, pending])
        db.commit()
        active_id, pending_id = active.id, pending.id
    with TestClient(app):
        with SessionLocal() as db:
            assert db.get(DocumentIndex, active_id).active
            recovered = db.get(DocumentIndex, pending_id)
            assert recovered.status == "failed" and not recovered.active


def test_queue_full_and_dimension_failure(client, project, monkeypatch):
    from pathlib import Path

    content = (
        Path(__file__).resolve().parents[1] / "data/business/quarterly_report.pdf"
    ).read_bytes()
    source = client.post(
        f"/api/projects/{project}/sources", files={"file": ("report.pdf", content)}
    ).json()["id"]
    path = f"/api/projects/{project}/sources/{source}/indexes"
    monkeypatch.setattr(index_queue.queue, "full", lambda: True)
    assert client.post(path, json={}).status_code == 503
    assert not client.get(f"/api/projects/{project}/indexes").json()
    monkeypatch.setattr(index_queue.queue, "full", lambda: False)

    class WrongDimension:
        def profile(self):
            return {"model": "dimension-test", "revision": "1", "dimension": 3}

        def chunks(self, text):
            return [{"content": text, "start": 0, "end": len(text)}]

        def encode(self, texts, **_):
            return [[1.0, 0.0] for _ in texts]

        def close(self):
            pass

    monkeypatch.setattr(embeddings, "provider", WrongDimension())
    value = client.post(path, json={}).json()
    from uuid import UUID

    for _ in range(100):
        with SessionLocal() as db:
            index = db.scalar(select(DocumentIndex).where(DocumentIndex.id == UUID(value["id"])))
            if index.status == "failed":
                assert "维度" in index.error and not index.active
                break
        time.sleep(0.05)
    else:
        raise AssertionError("Dimension mismatch did not fail")
