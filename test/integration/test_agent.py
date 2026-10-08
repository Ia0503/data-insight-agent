import asyncio
import json
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.agent import model as models
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.agent import AgentRun
from app.models.analysis import DatasetMapping
from app.schemas.agent import RunInput
from app.services.agent_runs import agent_queue, finish_run
from app.services.analysis import FIELDS
from sqlalchemy import func, select

DATA = Path(__file__).resolve().parents[1] / "data/business"


def source(client, project):
    response = client.post(
        f"/api/projects/{project}/sources",
        files={"file": ("orders.csv", (DATA / "orders.csv").read_bytes())},
    )
    assert response.status_code == 201
    identifier = response.json()["id"]
    mapped = client.put(
        f"/api/projects/{project}/sources/{identifier}/mapping",
        json={"fields": {field: field for field in FIELDS}},
    )
    assert mapped.json()["valid"]
    return identifier


def draft():
    return {
        "title": "销售分析报告",
        "summary": "",
        "findings": [
            {"kind": "fact", "fact_id": "R1:trend"},
            {
                "kind": "unknown",
                "text": "现有统计不能证明下降的原因。",
                "evidence_ids": [],
            },
        ],
        "recommendations": ["进一步核对地区变化及客户反馈。"],
        "limitations": ["这是虚构数据的流程测试，不是模型质量评测。"],
        "metric_ids": ["R1"],
        "chart_specs": [{"title": "地区净销售额", "result_id": "R1", "kind": "bar"}],
    }


class ScriptedModel:
    def __init__(self, source_id, mode="normal"):
        self.config = models.ModelConfig(
            "custom", "test-only-model", "https://example.invalid", "test-only-token"
        )
        self.source_id, self.mode, self.calls = source_id, mode, 0
        self.started, self.release = threading.Event(), threading.Event()
        self.closed = False

    async def complete(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            context = json.loads(messages[1]["content"])
            assert all(
                set(source)
                == {"id", "filename", "kind", "mapping_fields", "has_index", "structure"}
                for source in context["sources"]
            )
        self.started.set()
        if self.mode == "hold":
            while not self.release.is_set():
                await asyncio.sleep(0.02)
        if self.mode == "error":
            raise models.ModelError("模型鉴权失败，请核对配置。")
        if self.calls == 1:
            content = json.dumps(
                {
                    "steps": ["计算净销售额并比较基期。", "区分事实与未知并生成报告。"],
                    "requirements": [
                        {
                            "kind": "metric",
                            "description": "九月净销售额及基期地区比较。",
                            "source_id": context["sources"][0]["id"],
                            "metric": {
                                "metric": "net_sales",
                                "start": "2026-09-01",
                                "end": "2026-09-30",
                                "group": "region",
                                "compare_previous": True,
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            )
        elif tools and (self.calls == 2 or self.mode == "loop"):
            arguments = {
                "source_id": self.source_id,
                "metric": "net_sales",
                "start": "2026-09-01",
                "end": "2026-09-30",
                "compare_previous": True,
                "group": "region",
            }
            name = "calculate_metric"
            if self.mode == "bad_tool":
                name = "execute_code"
            if self.mode == "bad_args":
                arguments["code"] = "forbidden"
            return models.Reply(
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "private-test-reasoning",
                    "tool_calls": [
                        {
                            "id": f"call-{self.calls}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                {},
            )
        elif tools:
            content = "已有足够统计，生成报告并说明限制。"
        else:
            value = draft()
            if self.mode == "bad_report" or (self.mode == "repair_report" and self.calls == 4):
                value["findings"][0]["fact_id"] = "invented"
            content = json.dumps(value, ensure_ascii=False)
        return models.Reply(
            {"role": "assistant", "content": content},
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    async def close(self):
        self.closed = True


def enable_fake(monkeypatch, model):
    monkeypatch.setattr(get_settings(), "llm_calls_enabled", True)
    monkeypatch.setattr(models, "resolve_config", lambda *args, **kwargs: model.config)
    monkeypatch.setattr(models, "create_model", lambda config: model)


def submit(client, project, source_id, request_id=None):
    return client.post(
        f"/api/projects/{project}/agent/runs",
        json={
            "question": "比较九月和八月净销售额，按地区分组。",
            "source_ids": [source_id],
            "request_id": str(request_id or uuid4()),
        },
    )


def wait(client, project, run_id):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        response = client.get(f"/api/projects/{project}/agent/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.04)
    pytest.fail("Agent task did not finish")


def test_disabled_config_never_creates_or_calls_model(client, project, monkeypatch):
    identifier = source(client, project)
    monkeypatch.setattr(
        models,
        "create_model",
        lambda config: pytest.fail("Real model creation forbidden"),
    )
    configuration = client.get(f"/api/projects/{project}/agent/configuration").json()
    assert configuration["enabled"] is False
    assert not any(
        key in json.dumps(configuration).lower()
        for key in ("api_key", "base_url", "test-only-token")
    )
    response = submit(client, project, identifier)
    assert response.status_code == 503 and "尚未启用" in response.text


def test_real_tools_report_idempotency_and_project_isolation(client, project, monkeypatch, caplog):
    identifier = source(client, project)
    fake = ScriptedModel(identifier)
    enable_fake(monkeypatch, fake)
    request_id = uuid4()
    created = submit(client, project, identifier, request_id)
    assert created.status_code == 202
    run_id = created.json()["id"]
    assert submit(client, project, identifier, request_id).json()["id"] == run_id
    run = wait(client, project, run_id)
    assert run["status"] == "succeeded", run["error"]
    metric = run["report"]["metrics"][0]
    assert metric["value"] == "76000.00" and metric["comparison"]["change_percent"] == "-24.00"
    assert {row["group"]: row["value"] for row in metric["groups"]} == {
        # 独立手算：华东 (20000-1000)+20000；华西 (20000-3000)+20000。
        "华东": "39000.00",
        "华西": "37000.00",
    }
    assert fake.calls == 4 and fake.closed and run["tool_count"] == 1
    assert run["usage"]["total_tokens"] == 45 and run["usage"]["complete"] is False
    events = client.get(f"/api/projects/{project}/agent/runs/{run_id}/events").json()
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    for forbidden in ("private-test-reasoning", "test-only-token"):
        assert forbidden not in json.dumps(run) + json.dumps(events) + caplog.text
    other = client.post("/api/projects", json={"name": "Other(test)"}).json()["id"]
    for suffix in ("", "/events", "/cancel"):
        response = (
            client.post(f"/api/projects/{other}/agent/runs/{run_id}{suffix}")
            if suffix == "/cancel"
            else client.get(f"/api/projects/{other}/agent/runs/{run_id}{suffix}")
        )
        assert response.status_code == 404
    assert submit(client, other, identifier).status_code == 404
    changed = client.post(
        f"/api/projects/{project}/agent/runs",
        json={
            "question": "另一问题",
            "source_ids": [identifier],
            "request_id": str(request_id),
        },
    )
    assert changed.status_code == 409


@pytest.mark.parametrize(
    "mode,notice,calls",
    [
        ("bad_tool", "未允许", 2),
        ("bad_args", "白名单", 2),
        ("bad_report", "校验", 5),
        ("error", "鉴权", 1),
    ],
)
def test_model_failures_do_not_retry_or_publish_report(
    client, project, monkeypatch, mode, notice, calls
):
    identifier = source(client, project)
    fake = ScriptedModel(identifier, mode)
    enable_fake(monkeypatch, fake)
    run = wait(client, project, submit(client, project, identifier).json()["id"])
    assert run["status"] == "failed" and notice in run["error"]
    assert run["report"] is None and fake.calls == calls and fake.closed


def test_cancel_aborts_model_wait_without_new_requests(client, project, monkeypatch):
    identifier = source(client, project)
    fake = ScriptedModel(identifier, "hold")
    enable_fake(monkeypatch, fake)
    run_id = submit(client, project, identifier).json()["id"]
    assert fake.started.wait(2)
    assert client.post(f"/api/projects/{project}/agent/runs/{run_id}/cancel").status_code == 200
    run = wait(client, project, run_id)
    assert run["status"] == "cancelled" and run["report"] is None
    assert fake.calls == 1 and fake.closed


def test_mapping_change_and_cross_scope_stop_tools(client, project, monkeypatch):
    identifier = source(client, project)
    fake = ScriptedModel(identifier, "hold")
    enable_fake(monkeypatch, fake)
    run_id = submit(client, project, identifier).json()["id"]
    assert fake.started.wait(2)
    with SessionLocal() as db:
        mapping = db.get(DatasetMapping, UUID(identifier))
        mapping.version = uuid4()
        db.commit()
    fake.release.set()
    run = wait(client, project, run_id)
    assert run["status"] == "failed" and "映射已改变" in run["error"]
    assert run["tool_count"] == 0
    fake = ScriptedModel(str(uuid4()))
    enable_fake(monkeypatch, fake)
    run = wait(client, project, submit(client, project, identifier).json()["id"])
    assert run["status"] == "failed" and "选中的数据范围" in run["error"]


def test_tool_budget_and_queue_capacity(client, project, monkeypatch):
    identifier = source(client, project)
    fake = ScriptedModel(identifier, "loop")
    enable_fake(monkeypatch, fake)
    run = wait(client, project, submit(client, project, identifier).json()["id"])
    assert run["status"] == "succeeded" and run["tool_count"] == 12 and fake.calls == 14
    monkeypatch.setattr(agent_queue.queue, "full", lambda: True)
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.project_id == UUID(project))
        )
    assert submit(client, project, identifier).status_code == 503
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.project_id == UUID(project))
            )
            == count
        )


def test_finalize_retry_preserves_completed_report_without_recalling_model(
    client, project, monkeypatch
):
    from app.services import agent_runs

    identifier = source(client, project)
    fake = ScriptedModel(identifier)
    enable_fake(monkeypatch, fake)
    original = agent_runs.finish_run
    attempts = []

    def uncertain_commit(*args, **kwargs):
        original(*args, **kwargs)
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("test-only-secret-commit-confirmation")

    monkeypatch.setattr(agent_runs, "finish_run", uncertain_commit)
    run_id = submit(client, project, identifier).json()["id"]
    run = wait(client, project, run_id)
    assert run["status"] == "succeeded"
    until = time.monotonic() + 5
    while (len(attempts) < 2 or agent_queue.settling.is_set()) and time.monotonic() < until:
        time.sleep(0.05)
    assert not agent_queue.settling.is_set() and len(attempts) == 2 and fake.calls == 4
    finish_run(UUID(run_id), "failed", "should not replace success")
    assert (
        client.get(f"/api/projects/{project}/agent/runs/{run_id}").json()["status"] == "succeeded"
    )


def test_restart_marks_unfinished_without_auto_model_calls(client, project, monkeypatch):
    identifier = source(client, project)
    agent_queue.close()
    with SessionLocal() as db:
        from app.agent.tools import capture_snapshot

        run = AgentRun(
            project_id=UUID(project),
            request_id=uuid4(),
            question="未完成任务",
            provider="custom",
            model="test-only-model",
            snapshot=capture_snapshot(db, UUID(project), [UUID(identifier)]),
            status="running",
        )
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr(
        models,
        "create_model",
        lambda config: pytest.fail("Interrupted tasks must never resume model calls"),
    )
    agent_queue.start()
    result = client.get(f"/api/projects/{project}/agent/runs/{run_id}").json()
    assert result["status"] == "interrupted" and "不会自动" in result["error"]


def test_rejects_duplicate_scope_and_extra_input():
    with pytest.raises(ValueError):
        value = uuid4()
        RunInput(question="问题", source_ids=[value, value], request_id=uuid4())


def test_agent_business_dates_preserve_raw_values_and_complete_refund_statistics(client, project):
    from app.agent.tools import ToolExecutor, capture_snapshot

    identifier = source(client, project)
    with SessionLocal() as db:
        snapshot = capture_snapshot(db, UUID(project), [UUID(identifier)])
    executor = ToolExecutor(UUID(project), snapshot)
    rows = executor.execute("filter_data", json.dumps({"source_id": identifier}), "R1")["data"]
    fifth = next(row for row in rows["rows"] if row["record"] == 5)
    assert fifth["values"]["paid_at"] == "2026-08-31T16:00:00Z"
    assert fifth["business_date"] == "2026-09-01"
    assert next(row for row in rows["rows"] if row["record"] == 9)["business_date"] is None
    assert rows["business_date_context"]["timezone"] == "Asia/Shanghai"
    payload = {
        "source_id": identifier,
        "metric": "net_sales",
        "start": "2026-09-01",
        "end": "2026-09-30",
        "compare_previous": True,
    }
    result = executor.execute("calculate_metric", json.dumps(payload), "R2")["data"]
    assert result["statistics"]["paid_order_count"] == 4
    assert result["statistics"]["refund_order_count"] == 2
    assert result["comparison"]["statistics"]["paid_order_count"] == 4
    assert result["comparison"]["statistics"]["refund_order_count"] == 0
    assert result["comparison"]["evidence"]["records"] == [1, 2, 3, 4]
    payload.update(region="华东")
    scoped = executor.execute("calculate_metric", json.dumps(payload), "R3")["data"]
    assert scoped["statistics"]["paid_order_count"] == 2
    assert scoped["statistics"]["refund_order_count"] == 1
    payload.update(start="2027-01-01", end="2027-01-31", compare_previous=False)
    empty = executor.execute("calculate_metric", json.dumps(payload), "R4")["data"]
    assert empty["statistics"]["paid_order_count"] == empty["statistics"]["refund_order_count"] == 0


def test_old_report_is_returned_unchanged_without_new_draft_validation(client, project):
    old = {
        "title": "历史报告",
        "summary": "历史摘要保留。",
        "findings": [{"kind": "fact", "text": "历史自由事实原样保留。", "evidence_ids": []}],
        "metrics": [],
        "sources": [],
        "recommendations": [],
        "limitations": [],
        "chart_specs": [],
    }
    with SessionLocal() as db:
        run = AgentRun(
            project_id=UUID(project),
            request_id=uuid4(),
            question="历史任务",
            provider="custom",
            model="test-only-model",
            snapshot={"sources": []},
            status="succeeded",
            report=old,
        )
        db.add(run)
        db.commit()
        identifier = run.id
    response = client.get(f"/api/projects/{project}/agent/runs/{identifier}")
    assert response.status_code == 200
    assert response.json()["report"] == old


def test_workflow_reserves_report_calls_before_reasoning_exhausts_model_budget():
    from types import SimpleNamespace

    from app.agent.graph import AgentWorkflow

    events = []

    async def emit(kind, message, **changes):
        events.append(kind)

    workflow = AgentWorkflow(SimpleNamespace(), None, None, emit)
    workflow.model_calls = 14
    state = asyncio.run(workflow.reason({"results": [], "messages": []}))
    assert state == {"calls": []} and events == ["budget"]


@pytest.mark.parametrize("total_timeout", [False, True])
def test_model_and_total_deadlines_stop_without_retry(client, project, monkeypatch, total_timeout):
    from dataclasses import replace

    identifier = source(client, project)
    fake = ScriptedModel(identifier, "hold")
    if total_timeout:
        monkeypatch.setattr(get_settings(), "agent_timeout_seconds", 0.1)
    else:
        fake.config = replace(fake.config, timeout=0.1)
    enable_fake(monkeypatch, fake)
    run = wait(client, project, submit(client, project, identifier).json()["id"])
    assert run["status"] == "failed" and run["report"] is None
    assert "期限" in run["error"] if total_timeout else "超时" in run["error"]
    assert fake.calls == 1 and fake.closed


def test_search_uses_captured_historical_index_and_returns_document_evidence(
    client, project, monkeypatch
):
    from app.agent.report import validate_report
    from app.agent.tools import ToolExecutor, capture_snapshot
    from app.models.analysis import DocumentChunk, DocumentIndex
    from app.models.workspace import DataSource
    from app.schemas.agent import ReportDraft
    from app.services import embeddings, retrieval
    from app.services.analysis import file_hash
    from app.services.sources import source_path

    response = client.post(
        f"/api/projects/{project}/sources",
        files={"file": ("feedback.csv", (DATA / "feedback.csv").read_bytes())},
    )
    source_id = UUID(response.json()["id"])
    profile = {"model": "test-only-vector", "revision": "test", "dimension": 2}
    with SessionLocal() as db:
        origin = db.get(DataSource, source_id)
        old = DocumentIndex(
            project_id=UUID(project),
            source_id=source_id,
            signature="old",
            content_hash=file_hash(source_path(origin)),
            profile=profile,
            options={},
            status="ready",
            active=True,
            chunk_count=1,
        )
        db.add(old)
        db.flush()
        chunk = DocumentChunk(
            index_id=old.id,
            ordinal=0,
            record=1,
            record_id="F001",
            start=0,
            end=3,
            content="旧证据",
            embedding=[1, 0],
        )
        db.add(chunk)
        db.commit()
        snapshot = capture_snapshot(db, UUID(project), [source_id])
        old.active = False
        db.flush()
        new = DocumentIndex(
            project_id=UUID(project),
            source_id=source_id,
            signature="new",
            content_hash=old.content_hash,
            profile=profile,
            options={},
            status="ready",
            active=True,
            chunk_count=1,
        )
        db.add(new)
        db.flush()
        db.add(
            DocumentChunk(
                index_id=new.id,
                ordinal=0,
                record=1,
                start=0,
                end=3,
                content="新证据",
                embedding=[1, 0],
            )
        )
        db.commit()
        chunk_id = str(chunk.id)
    monkeypatch.setattr(retrieval, "profile", lambda: profile)
    monkeypatch.setattr(embeddings.provider, "encode", lambda *args, **kwargs: [[1, 0]])
    executor = ToolExecutor(UUID(project), snapshot)
    result = executor.execute(
        "search_evidence",
        json.dumps({"query": "退款", "source_ids": [str(source_id)]}),
        "R1",
    )
    assert [hit["text"] for hit in result["data"]["results"]] == ["旧证据"]
    evidence_id = f"R1:{chunk_id}"
    report = validate_report(
        ReportDraft(
            title="反馈分析",
            summary="",
            findings=[
                {
                    "kind": "fact",
                    "fact_id": f"{evidence_id}:quote",
                }
            ],
            limitations=["不能代表所有客户。"],
        ),
        [result],
    )
    assert (
        report["sources"][0]["kind"] == "document" and report["sources"][0]["chunk_id"] == chunk_id
    )


def test_changed_original_file_stops_before_tools(client, project, monkeypatch):
    from app.models.workspace import DataSource
    from app.services.sources import source_path

    identifier = source(client, project)
    fake = ScriptedModel(identifier, "hold")
    enable_fake(monkeypatch, fake)
    run_id = submit(client, project, identifier).json()["id"]
    assert fake.started.wait(2)
    with SessionLocal() as db:
        path = source_path(db.get(DataSource, UUID(identifier)))
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        fake.release.set()
        run = wait(client, project, run_id)
        assert run["status"] == "failed" and "原文件" in run["error"] and run["tool_count"] == 0
    finally:
        path.write_bytes(original)


def test_structure_repair_is_single_and_valid_report_can_recover(client, project, monkeypatch):
    identifier = source(client, project)
    fake = ScriptedModel(identifier, "repair_report")
    enable_fake(monkeypatch, fake)
    run_id = submit(client, project, identifier).json()["id"]
    run = wait(client, project, run_id)
    assert run["status"] == "succeeded" and fake.calls == 5
    events = client.get(f"/api/projects/{project}/agent/runs/{run_id}/events").json()
    assert sum(event["kind"] == "repair" for event in events) == 1


def test_repeated_invalid_plan_stops_after_one_repair(client, project, monkeypatch):
    identifier = source(client, project)
    fake = ScriptedModel(identifier)

    async def invalid(messages, tools=None):
        fake.calls += 1
        return models.Reply({"role": "assistant", "content": "not-json"}, {})

    fake.complete = invalid
    enable_fake(monkeypatch, fake)
    run = wait(client, project, submit(client, project, identifier).json()["id"])
    assert run["status"] == "failed" and "一次修复" in run["error"]
    assert fake.calls == 2 and run["tool_count"] == 0
