import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "agent_live_evaluation", Path(__file__).resolve().parents[1] / "evaluation/run_agent_live.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def missing_report():
    return {
        "status": "succeeded",
        "report": {
            "sources": [],
            "chart_specs": [],
            "metrics": [
                {
                    "metric": "net_sales",
                    "value": "76000.00",
                    "filters": {"start": "2026-09-01", "end": "2026-09-30"},
                }
            ],
            "findings": [
                {"kind": "unknown", "text": "广告回报缺少成本数据。"},
                {"kind": "unknown", "text": "未来收入预测缺少建模数据。"},
            ],
        },
    }


def test_missing_case_accepts_verified_history_without_guessing_unknowns():
    result = evaluation.verify_case("missing", missing_report(), [], None, "")
    assert result["missing_data_unknown"] is True


def two_month_context_nets():
    return [
        {
            "filters": {"start": "2026-08-01", "end": "2026-09-30", "group": "month"},
            "value": "176000.00",
            "evidence": {"records": list(range(1, 9))},
            "groups": [
                {"group": "2026-08", "value": "100000.00"},
                {"group": "2026-09", "value": "76000.00"},
            ],
        },
        {
            "filters": {"start": "2026-09-01", "end": "2026-09-30"},
            "value": "76000.00",
            "evidence": {"records": [5, 6, 7, 8]},
        },
    ]


def test_net_evaluation_accepts_independently_verified_two_month_context():
    nets = two_month_context_nets()
    assert evaluation.verify_net_periods(nets) == [nets[1]]


@pytest.mark.parametrize("failure", ["total", "month", "records"])
def test_two_month_context_still_rejects_wrong_total_month_or_original_records(failure):
    nets = two_month_context_nets()
    if failure == "total":
        nets[0]["value"] = "180000.00"
    elif failure == "month":
        nets[0]["groups"][1]["value"] = "80000.00"
    else:
        nets[0]["evidence"]["records"].remove(8)
    with pytest.raises(RuntimeError):
        evaluation.verify_net_periods(nets)


@pytest.mark.parametrize(
    "failure", ["value", "future_date", "missing_unknown", "invented_forecast"]
)
def test_missing_case_rejects_wrong_history_or_unsupported_forecast(failure):
    run = missing_report()
    report = run["report"]
    if failure == "value":
        report["metrics"][0]["value"] = "900000.00"
    elif failure == "future_date":
        report["metrics"][0]["filters"]["end"] = "2027-09-30"
    elif failure == "missing_unknown":
        report["findings"].pop()
    else:
        report["findings"].append({"kind": "fact", "text": "未来收入为一百万元。"})
    with pytest.raises(RuntimeError):
        evaluation.verify_case("missing", run, [], None, "")


def test_report_completion_retains_controlled_failure_diagnostics(monkeypatch):
    import asyncio

    from app.agent.graph import AgentWorkflow
    from app.agent.model import ChatModel, ModelConfig, ModelError

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "evaluation"))
    import complete_agent_report

    async def check(self, state):
        return {}

    async def report(self, state):
        return await self.ask([{"role": "user", "content": "mock-only"}])

    async def fail(self, messages, tools=None):
        raise ModelError("模型服务请求失败，没有自动重试。")

    monkeypatch.setattr(AgentWorkflow, "check", check)
    monkeypatch.setattr(AgentWorkflow, "report", report)
    monkeypatch.setattr(ChatModel, "complete", fail)
    key = "test-only-probe-key"
    result = asyncio.run(
        complete_agent_report.generate(
            {"project_id": str(uuid4()), "question": "mock-only", "snapshot": {"sources": []}},
            [],
            ModelConfig("custom", "mock-only", "https://example.invalid", key),
        )
    )
    assert result["status"] == "failed"
    assert result["actual_model_requests"] == 1
    assert result["error"] == "模型服务请求失败，没有自动重试。"
    assert result["events"][0]["kind"] == "model"
    assert key not in json.dumps(result)


def test_report_diagnostics_locate_reference_digits_without_storing_text(monkeypatch):
    from app.agent.model import Reply

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "evaluation"))
    import complete_agent_report

    payload = {
        "title": "工具 R1 分析报告，业务内容不应出现在诊断中。",
        "summary": "",
        "findings": [{"kind": "unknown", "text": "原因尚待确认。", "evidence_ids": []}],
        "limitations": ["虚构资料。"],
    }
    diagnostic = complete_agent_report.inspect_reply(
        Reply({"content": json.dumps(payload)}, {}),
        [{"id": "R1", "tool": "filter_data", "data": {}}],
    )
    assert diagnostic["schema_valid"] is True
    assert diagnostic["constraints_valid"] is False
    assert diagnostic["digit_fields"] == ["title"]
    assert diagnostic["result_ids_in_narrative"] == ["R1"]
    assert payload["title"] not in json.dumps(diagnostic, ensure_ascii=False)


def test_report_schema_diagnostics_redact_unknown_field_name_and_value(monkeypatch):
    from app.agent.model import Reply

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "evaluation"))
    import complete_agent_report

    payload = {
        "title": "分析报告",
        "summary": "",
        "findings": [{"kind": "unknown", "text": "缺少资料。", "evidence_ids": []}],
        "limitations": ["虚构资料。"],
        "test-only-secret-field": "test-only-secret-value",
    }
    diagnostic = complete_agent_report.inspect_reply(
        Reply({"content": json.dumps(payload)}, {}), []
    )
    assert diagnostic["schema_valid"] is False
    assert diagnostic["errors"] == [{"type": "extra_forbidden", "path": ["unknown_field"]}]
    assert "test-only-secret" not in json.dumps(diagnostic)


def monthly_nets():
    return [
        {
            "value": "76000.00",
            "filters": {"start": "2026-09-01", "end": "2026-09-30", "group": "region"},
            "groups": [
                {"group": "华东", "value": "39000.00"},
                {"group": "华西", "value": "37000.00"},
            ],
            "evidence": {"records": [5, 6, 7, 8]},
        },
        {
            "value": "100000.00",
            "filters": {"start": "2026-08-01", "end": "2026-08-31", "group": "region"},
            "groups": [
                {"group": "华东", "value": "50000.00"},
                {"group": "华西", "value": "50000.00"},
            ],
            "evidence": {"records": [1, 2, 3, 4]},
        },
    ]


def test_net_evaluation_separates_august_and_september():
    nets = monthly_nets()
    assert evaluation.verify_net_periods(nets) == [nets[0]]


@pytest.mark.parametrize(
    "fault", ["august_records", "august_value", "september_groups", "no_september"]
)
def test_net_evaluation_still_rejects_wrong_month_values_and_provenance(fault):
    nets = monthly_nets()
    if fault == "august_records":
        nets[1]["evidence"]["records"] = [5, 6, 7, 8]
    elif fault == "august_value":
        nets[1]["value"] = "76000.00"
    elif fault == "september_groups":
        nets[0]["groups"][0]["value"] = "50000.00"
    else:
        nets.pop(0)
    with pytest.raises(RuntimeError):
        evaluation.verify_net_periods(nets)
