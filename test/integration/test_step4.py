import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.agent import AgentEvent, AgentRun
from app.models.analysis import DatasetMapping, DocumentIndex
from app.models.workspace import DataSource, Project
from app.services import agent_stream, examples
from app.services.analysis import file_hash
from app.services.sources import source_path
from sqlalchemy import func, select


def insert_run(project, *, question="测试问题", at=None, status="succeeded", title=None, count=0):
    with SessionLocal() as db:
        run = AgentRun(
            project_id=UUID(project),
            request_id=uuid4(),
            question=question,
            provider="test",
            model="fake",
            status=status,
            snapshot={},
            report={"title": title} if title else None,
            results=[{"private": "PRIVATE_RAW_TOOL"}],
            created_at=at or datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        for sequence in range(1, count + 1):
            db.add(
                AgentEvent(
                    run_id=run.id, sequence=sequence, kind="tool_end", message=f"公开记录{sequence}"
                )
            )
        db.commit()
        return str(run.id)


def test_history_cursor_dates_keywords_and_scope(client, project):
    p = f"/api/projects/{project}/agent"
    ids = [
        insert_run(
            project,
            question=f"历史问题{i}",
            at=datetime(2026, 10, 6, 16, tzinfo=timezone.utc),
            title="标题含%_字面值" if i == 0 else None,
        )
        for i in range(25)
    ]
    insert_run(project, question="前一日", at=datetime(2026, 10, 6, 15, 59, tzinfo=timezone.utc))
    response = client.get(
        p + "/history", params={"start": "2026-10-07", "end": "2026-10-07", "limit": 7}
    )
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["total"] == 25 and len(first["items"]) == 7
    seen = [v["id"] for v in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = client.get(
            p + "/history",
            params={"start": "2026-10-07", "end": "2026-10-07", "limit": 7, "cursor": cursor},
        ).json()
        seen.extend(v["id"] for v in page["items"])
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == 25 and set(seen) == set(ids)
    literal = client.get(p + "/history", params={"q": "%_"}).json()
    assert literal["total"] == 1 and literal["items"][0]["report_title"] == "标题含%_字面值"
    insert_run(project, question="English Alpha")
    assert client.get(p + "/history", params={"q": "ALPHA"}).json()["total"] == 1
    assert client.get(p + "/history", params={"status": "failed"}).json()["total"] == 0
    other = client.post("/api/projects", json={"name": "历史隔离(test)"}).json()["id"]
    assert client.get(f"/api/projects/{other}/agent/history").json()["total"] == 0
    assert isinstance(client.get(p + "/runs").json(), list)
    assert len(client.get(p + "/runs").json()) == 20


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "not-json"},
        {"cursor": base64.urlsafe_b64encode(b'{"at":1,"id":2}').decode()},
        {"start": "2026-10-08", "end": "2026-10-07"},
        {"status": "unknown"},
        {"end": "9999-12-31"},
        {"limit": 0},
    ],
)
def test_history_invalid_inputs_are_controlled(client, project, params):
    assert client.get(f"/api/projects/{project}/agent/history", params=params).status_code == 422


def test_terminal_stream_replays_all_pages_resumes_and_hides_private_data(client, project):
    identifier = insert_run(project, count=250)
    path = f"/api/projects/{project}/agent/runs/{identifier}/stream"
    response = client.get(path)
    assert response.status_code == 200 and response.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert response.text.count("event: progress") == 250
    assert "id: 250\n" in response.text and "event: complete" in response.text
    assert "PRIVATE_RAW_TOOL" not in response.text and "snapshot" not in response.text
    resumed = client.get(path, params={"after": 100}, headers={"Last-Event-ID": "150"})
    assert resumed.text.count("event: progress") == 100 and "id: 151\n" in resumed.text
    assert "id: 150\n" not in resumed.text
    for header in ("-1", "bad", "99999999999"):
        assert client.get(path, headers={"Last-Event-ID": header}).status_code == 422
    other = client.post("/api/projects", json={"name": "流隔离(test)"}).json()["id"]
    assert client.get(f"/api/projects/{other}/agent/runs/{identifier}/stream").status_code == 404


def test_stream_database_failure_is_controlled_and_does_not_run_model(monkeypatch, caplog):
    class Request:
        async def is_disconnected(self):
            return False

    def unavailable(*args):
        raise RuntimeError("PRIVATE_DATABASE_VALUE")

    monkeypatch.setattr(agent_stream, "read_progress", unavailable)

    async def collect():
        return "".join(
            [v async for v in agent_stream.stream_progress(Request(), uuid4(), uuid4(), 0)]
        )

    value = asyncio.run(collect())
    assert "event: unavailable" in value and "PRIVATE_DATABASE_VALUE" not in value + caplog.text


@pytest.fixture
def example(monkeypatch, tmp_path):
    monkeypatch.setattr(examples, "PROJECT_ID", uuid4())
    monkeypatch.setattr(examples, "NAME", f"业务示例-{uuid4()}(test)")
    monkeypatch.setattr(examples, "MARKER", str(uuid4()))
    monkeypatch.setattr(get_settings(), "embedding_dir", tmp_path / "missing-model")
    return "/api/examples/business"


def test_example_concurrent_prepare_import_and_mapping_preservation(client, example):
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: client.post(example).json(), range(2)))
    assert values[0]["id"] == values[1]["id"] and values[0]["name"].endswith("(test)")
    project = values[0]["id"]
    imported = {}
    for filename in examples.ASSETS:
        response = client.post(example + "/files/" + filename)
        assert response.status_code == 200, response.text
        source = response.json()["source"]
        assert source["status"] == "ready"
        imported[filename] = source["id"]
    with SessionLocal() as db:
        mapping = db.get(DatasetMapping, UUID(imported["orders.csv"]))
        version = mapping.version
    for filename, identifier in imported.items():
        assert client.post(example + "/files/" + filename).json()["source"]["id"] == identifier
    with SessionLocal() as db:
        assert db.get(DatasetMapping, UUID(imported["orders.csv"])).version == version
        assert (
            db.scalar(
                select(func.count())
                .select_from(DataSource)
                .where(DataSource.project_id == UUID(project))
            )
            == 5
        )
    status = client.get(example).json()
    assert all(v["status"] == "ready" for v in status["files"])
    assert not status["model_downloaded"]
    assert client.post(example + "/files/missing.txt").status_code == 404


def test_example_refuses_changed_original_and_keeps_queued_index(
    client, example, monkeypatch, tmp_path
):
    result = client.post(example + "/files/feedback.csv").json()["source"]
    identifier = UUID(result["id"])
    with SessionLocal() as db:
        source = db.get(DataSource, identifier)
        path = source_path(source)
        index = DocumentIndex(
            project_id=source.project_id,
            source_id=source.id,
            signature="a" * 64,
            content_hash=file_hash(path),
            profile={},
            options={},
            status="queued",
        )
        db.add(index)
        db.commit()

    def forbidden(*args):
        raise AssertionError("Must not submit a second queued index")

    model_dir = tmp_path / "available-model"
    model_dir.mkdir()
    (model_dir / "manifest.json").write_text("{}")
    monkeypatch.setattr(get_settings(), "embedding_dir", model_dir)
    monkeypatch.setattr(examples.index_queue, "submit", forbidden)
    assert client.post(example + "/files/feedback.csv").status_code == 200
    path.write_bytes(path.read_bytes() + b"\n")
    assert client.post(example + "/files/feedback.csv").status_code == 409
    assert path.read_bytes().endswith(b"\n\n")


def test_partial_example_import_is_manually_retryable(client, example, monkeypatch):
    original = examples.process_source

    def fail(db, source):
        source.status = "failed"
        source.error = "暂时失败"
        db.commit()
        return source

    monkeypatch.setattr(examples, "process_source", fail)
    failed = client.post(example + "/files/orders.csv").json()["source"]
    assert failed["status"] == "failed"
    monkeypatch.setattr(examples, "process_source", original)
    succeeded = client.post(example + "/files/orders.csv").json()["source"]
    assert succeeded["id"] == failed["id"] and succeeded["status"] == "ready"


def test_renamed_legacy_example_remains_identifiable_by_assets(client, example, monkeypatch):
    with SessionLocal() as db:
        legacy = Project(id=uuid4(), name=examples.NAME, description=examples.DESCRIPTION)
        db.add(legacy)
        db.commit()
        identifier = str(legacy.id)
    assert client.post(example).json()["id"] == identifier
    assert client.post(example + "/files/orders.csv").json()["source"]["project_id"] == identifier
    client.put(
        f"/api/projects/{identifier}", json={"name": "重命名示例(test)", "description": "已编辑"}
    )
    assert client.post(example).json()["id"] == identifier
    assert client.get(example).json()["project"]["name"] == "重命名示例(test)"


def test_stream_heartbeats_and_disconnect_release_the_generator(monkeypatch, caplog):
    class Request:
        calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 1

    reads = []

    def read(*args):
        reads.append(args)
        return {"status": "running"}, []

    async def sleep(_):
        pass

    clock = iter((0, 16, 16))
    monkeypatch.setattr(agent_stream, "monotonic", lambda: next(clock))
    monkeypatch.setattr(agent_stream, "read_progress", read)
    monkeypatch.setattr(agent_stream.asyncio, "sleep", sleep)

    async def collect():
        return "".join(
            [v async for v in agent_stream.stream_progress(Request(), uuid4(), uuid4(), 0)]
        )

    caplog.set_level("INFO", logger="app.services.agent_stream")
    value = asyncio.run(collect())
    assert "event: heartbeat" in value and "event: complete" not in value
    assert len(reads) == 1 and "event=agent_stream_closed" in caplog.text
