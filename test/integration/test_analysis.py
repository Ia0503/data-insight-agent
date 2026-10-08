import json
import threading
import time
from pathlib import Path

import pytest
from app.core.database import SessionLocal
from app.models.analysis import DocumentChunk, DocumentIndex
from app.models.workspace import DataSource
from app.services import embeddings
from app.services.analysis import FIELDS

DATA = Path(__file__).resolve().parents[1] / "data" / "business"
EXPECTED = json.loads((DATA / "expected.json").read_text("utf-8"))


def upload(client, project, name, content=None):
    result = client.post(
        f"/api/projects/{project}/sources",
        files={"file": (name, content if content is not None else (DATA / name).read_bytes())},
    )
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "ready", result.text
    return result.json()["id"]


def test_metric_golden_and_generic_tools(client, project):
    source = upload(client, project, "orders.csv")
    path = f"/api/projects/{project}/sources/{source}"
    assert (
        client.post(
            path + "/metrics", json={"start": "2026-09-01", "end": "2026-09-30"}
        ).status_code
        == 409
    )
    assert client.put(path + "/mapping", json={"fields": {f: f for f in FIELDS}}).json()["valid"]
    for metric, expected in EXPECTED["september"].items():
        if metric in {"region_net", "records"}:
            continue
        result = client.post(
            path + "/metrics",
            json={
                "metric": metric,
                "start": "2026-09-01",
                "end": "2026-09-30",
                "compare_previous": True,
                "group": "region",
            },
        )
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["value"] == expected["value"]
        assert data["comparison"]["change_percent"] == expected["change_percent"]
        assert data["evidence"]["records"] == EXPECTED["september"]["records"]
        if metric == "net_sales":
            assert {g["group"]: g["value"] for g in data["groups"]} == EXPECTED["september"][
                "region_net"
            ]
    filtered = client.post(
        path + "/tools",
        json={"tool": "filter_data", "filters": [{"field": "order_id", "value": "0005"}]},
    ).json()
    assert filtered["rows"][0]["values"]["order_id"] == "0005"
    assert filtered["rows"][0]["record"] == 5
    grouped = client.post(
        path + "/tools",
        json={"tool": "group_by", "group": "region", "value": "paid_amount", "aggregation": "sum"},
    ).json()
    assert {g["group"]: g["value"] for g in grouped["groups"]} == {
        "华东": "90000.00",
        "华西": "90000.00",
    }
    assert (
        client.post(path + "/tools", json={"tool": "eval", "code": "print(1)"}).status_code == 422
    )
    assert (
        client.post(path + "/tools", json={"tool": "group_by", "group": "missing"}).status_code
        == 422
    )
    empty = client.post(
        path + "/metrics",
        json={"metric": "refund_rate", "start": "2027-01-01", "end": "2027-01-31"},
    ).json()
    assert empty["value"] is None
    assert (
        client.post(
            path + "/metrics", json={"start": "2026-10-01", "end": "2026-09-01"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "replacement",
    [
        ("0002", "0001"),
        ("25000.00,0", "25000.00,25000.01"),
        ("2026-08-01", "invalid-date"),
        ("2026-08-01", "9999-12-31T23:00:00-12:00"),
        ("25000.00,0", "25000.001,0"),
        ("华东,专业版", ",专业版"),
        ("25000.00,0", "NaN,0"),
    ],
)
def test_mapping_rejects_invalid_business_records(client, project, replacement):
    content = (DATA / "orders.csv").read_text("utf-8-sig").replace(*replacement, 1).encode()
    source = upload(client, project, "invalid_business.csv", content)
    result = client.put(
        f"/api/projects/{project}/sources/{source}/mapping", json={"fields": {f: f for f in FIELDS}}
    )
    assert result.status_code == 200
    assert not result.json()["valid"]
    assert result.json()["issue_count"] > 0


class FaultProvider:
    """Synthetic vectors test queue/rollback only; never used for semantic evaluation."""

    failed = False
    entered = threading.Event()
    release = threading.Event()
    block = False

    def profile(self):
        return {"model": "test-fault", "revision": "1", "dimension": 2}

    def chunks(self, text):
        return [{"content": text, "start": 0, "end": len(text)}]

    def encode(self, texts, *, query=False):
        if self.block:
            self.entered.set()
            assert self.release.wait(5)
        if self.failed:
            raise embeddings.EmbeddingError("测试模拟模型失败。")
        return [[1.0, 0.0] for _ in texts]

    def close(self):
        pass


def wait_index(client, project, index_id):
    for _ in range(200):
        value = next(
            i for i in client.get(f"/api/projects/{project}/indexes").json() if i["id"] == index_id
        )
        if value["status"] in {"ready", "failed"}:
            return value
        time.sleep(0.05)
    raise AssertionError("Index did not finish")


def test_index_rollback_dedup_isolation_and_dimension(client, project, monkeypatch):
    provider = FaultProvider()
    monkeypatch.setattr(embeddings, "provider", provider)
    source = upload(client, project, "quarterly_report.pdf")
    path = f"/api/projects/{project}"
    result = client.post(f"{path}/sources/{source}/indexes", json={})
    assert result.status_code == 202
    first = wait_index(client, project, result.json()["id"])
    assert first["status"] == "ready" and first["active"]
    assert first["chunk_count"] == 3
    assert client.post(f"{path}/sources/{source}/indexes", json={}).json()["id"] == first["id"]
    provider.failed = True
    failed = client.post(f"{path}/sources/{source}/indexes", json={"force": True}).json()
    assert wait_index(client, project, failed["id"])["status"] == "failed"
    versions = client.get(path + "/indexes").json()
    assert next(i for i in versions if i["id"] == first["id"])["active"]
    provider.failed = False
    results = client.post(path + "/search", json={"query": "test"})
    assert results.status_code == 200, results.text
    citation = results.json()["results"][0]
    assert (
        client.get(path + "/citations/" + citation["chunk_id"]).json()["text"] == citation["text"]
    )
    other = client.post("/api/projects", json={"name": "Another project(test)"}).json()["id"]
    assert client.get(f"/api/projects/{other}/citations/{citation['chunk_id']}").status_code == 404
    assert (
        client.post(
            f"/api/projects/{other}/search", json={"query": "test", "source_ids": [source]}
        ).status_code
        == 404
    )
    # 其他项目放入不同维度向量，验证过滤在距离运算前完成。
    with SessionLocal() as db:
        other_source = DataSource(
            project_id=other,
            filename="other.csv",
            storage_key=f"other-{other}.csv",
            kind="csv",
            size_bytes=1,
            status="ready",
        )
        db.add(other_source)
        db.flush()
        other_index = DocumentIndex(
            project_id=other,
            source_id=other_source.id,
            signature="x",
            content_hash="x",
            profile={"dimension": 3},
            options={},
            status="ready",
            active=True,
        )
        db.add(other_index)
        db.flush()
        db.add(
            DocumentChunk(
                index_id=other_index.id,
                ordinal=1,
                page=None,
                record=1,
                record_id=None,
                start=0,
                end=1,
                content="x",
                embedding=[1, 0, 0],
            )
        )
        db.commit()
    assert client.post(path + "/search", json={"query": "test"}).status_code == 200
    monkeypatch.setattr(
        provider, "profile", lambda: {"model": "different", "revision": "2", "dimension": 2}
    )
    assert client.post(path + "/search", json={"query": "test"}).status_code == 409


def test_index_concurrent_claim_and_feedback_locator(client, project, monkeypatch):
    provider = FaultProvider()
    provider.block = True
    provider.entered.clear()
    provider.release.clear()
    monkeypatch.setattr(embeddings, "provider", provider)
    source = upload(client, project, "feedback.csv")
    path = f"/api/projects/{project}/sources/{source}/indexes"
    assert client.post(path, json={}).status_code == 422
    value = client.post(path, json={"text_field": "text", "record_id_field": "feedback_id"})
    assert value.status_code == 202
    try:
        assert provider.entered.wait(5)
        assert client.post(path, json={"text_field": "text"}).status_code == 409
    finally:
        provider.release.set()
    assert wait_index(client, project, value.json()["id"])["status"] == "ready"
    with SessionLocal() as db:
        from uuid import UUID

        from sqlalchemy import select

        chunks = db.scalars(
            select(DocumentChunk).where(DocumentChunk.index_id == UUID(value.json()["id"]))
        ).all()
        assert {c.record: c.record_id for c in chunks} == {i: f"F{i:03d}" for i in range(1, 7)}


def test_first_mapping_save_is_serialized(client, project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.services import analysis

    source = upload(client, project, "orders.csv")
    original = analysis.validate_orders
    barrier = threading.Barrier(2)

    def simultaneous_validation(*args):
        result = original(*args)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(analysis, "validate_orders", simultaneous_validation)
    path = f"/api/projects/{project}/sources/{source}/mapping"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.put(path, json={"fields": {f: f for f in FIELDS}}), range(2))
        )
    assert all(result.status_code == 200 and result.json()["valid"] for result in results)
    assert len({result.json()["version"] for result in results}) == 2
    assert client.get(path).json()["version"] in {result.json()["version"] for result in results}


def test_generic_aggregate_overflow_returns_validation_error(client, project):
    source = upload(client, project, "overflow.csv", b"group,value\na,9e999999\na,9e999999\n")
    result = client.post(
        f"/api/projects/{project}/sources/{source}/tools",
        json={"tool": "group_by", "group": "group", "value": "value", "aggregation": "sum"},
    )
    assert result.status_code == 422
    assert "数值范围" in result.json()["detail"]


def test_claim_failure_recovers_after_database_returns(client, project, monkeypatch):
    from uuid import UUID

    from app.services import retrieval
    from sqlalchemy import func, select

    provider = FaultProvider()
    monkeypatch.setattr(embeddings, "provider", provider)
    source = upload(client, project, "quarterly_report.pdf")
    other = upload(client, project, "product_manual.pdf")
    path = f"/api/projects/{project}/sources/{source}/indexes"
    first = wait_index(client, project, client.post(path, json={}).json()["id"])
    claimed, recovering = threading.Event(), threading.Event()
    factory = retrieval.SessionLocal
    settle = retrieval.fail_index
    failures = 0

    def failing_claim_session():
        db = factory()
        if not claimed.is_set():

            def fail_commit():
                claimed.set()
                raise RuntimeError("PRIVATE_DATABASE_ERROR")

            db.commit = fail_commit
        return db

    def unavailable_settlement(index_id, message):
        nonlocal failures
        if failures < 2:
            failures += 1
            recovering.set()
            raise RuntimeError("PRIVATE_DATABASE_ERROR")
        settle(index_id, message)

    monkeypatch.setattr(retrieval, "SessionLocal", failing_claim_session)
    monkeypatch.setattr(retrieval, "fail_index", unavailable_settlement)
    second = client.post(path, json={"force": True}).json()
    assert claimed.wait(5) and recovering.wait(5)
    # 回收尚未成功时，不吞掉任务，也不继续接收新推理任务。
    assert retrieval.index_queue.queue.unfinished_tasks == 1
    assert (
        client.post(f"/api/projects/{project}/sources/{other}/indexes", json={}).status_code == 503
    )
    value = wait_index(client, project, second["id"])
    assert value["status"] == "failed" and not value["active"]
    assert "PRIVATE_DATABASE_ERROR" not in value["error"]
    with SessionLocal() as db:
        assert db.get(DocumentIndex, UUID(first["id"])).active
        assert (
            db.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.index_id == UUID(second["id"]))
            )
            == 0
        )
    # 故障解除后重新提交可成功，不需要重启服务。
    third = client.post(path, json={"force": True})
    assert third.status_code == 202, third.text
    assert wait_index(client, project, third.json()["id"])["status"] == "ready"


def test_lost_completion_acknowledgment_keeps_committed_index(client, project, monkeypatch):
    from app.services import retrieval

    monkeypatch.setattr(embeddings, "provider", FaultProvider())
    source = upload(client, project, "quarterly_report.pdf")
    original = retrieval.activate
    settled = threading.Event()
    original_settle = retrieval.fail_index

    def lose_acknowledgment(*args):
        original(*args)
        raise RuntimeError("SIMULATED_POST_COMMIT_FAILURE")

    def record_settlement(*args):
        original_settle(*args)
        settled.set()

    monkeypatch.setattr(retrieval, "activate", lose_acknowledgment)
    monkeypatch.setattr(retrieval, "fail_index", record_settlement)
    index = client.post(f"/api/projects/{project}/sources/{source}/indexes", json={}).json()
    assert settled.wait(5)
    value = wait_index(client, project, index["id"])
    assert value["status"] == "ready" and value["active"] and value["chunk_count"] == 3
