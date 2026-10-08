import asyncio
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

TEST = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("batch_runner", TEST / "evaluation/run_batch.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from evaluation import batch_scoring as scoring  # noqa: E402

DATA = json.loads((TEST / "data/evaluation/cases.json").read_text("utf-8"))
FILES = {key: TEST.parent / value for key, value in DATA["assets"].items()}


def answer(case):
    metrics = []
    for golden in case["expected_metrics"]:
        metrics.append(
            {
                "filename": golden["filename"],
                "metric": golden["metric"],
                "filters": {
                    key: golden.get(key) for key in ("start", "end", "region", "product", "group")
                },
                "value": golden["value"],
                "records": golden["records"],
                "groups": [{"group": k, "value": v} for k, v in golden.get("groups", {}).items()],
                "comparison": {
                    "value": golden["previous_value"],
                    "change_percent": golden["change_percent"],
                }
                if "previous_value" in golden
                else None,
            }
        )
    target = case["targets"][0] if case["targets"] else None
    selection = {FILES[a].name: FILES[a] for a in case["scope"]}
    evidence = (
        [
            {
                **target,
                "quote": scoring.original_text(
                    selection[target["filename"]],
                    page=target.get("page"),
                    record=target.get("record"),
                ),
            }
        ]
        if target
        else []
    )
    return {
        "title": "固定答案",
        "metrics": metrics,
        "evidence": evidence,
        "findings": [{"kind": "unknown", "text": "因果尚未确认。"}],
        "limitations": ["虚构小样本"],
    }, selection


@pytest.mark.parametrize(
    "case", [c for c in DATA["metrics"] if c["expected"]], ids=lambda c: c["id"]
)
def test_handwritten_truth_including_timezone_and_null_rates(case):
    expected = case["expected"]
    item = {
        "metric": expected["metric"],
        "filters": case["request"],
        "comparison": {"requested": True} if "previous_value" in expected else None,
    }
    independent = scoring.oracle(item, FILES[case["asset"]])
    assert all(
        scoring.metric_correct(
            {
                **independent,
                "groups": [
                    {"group": k, "value": v} for k, v in independent.get("groups", {}).items()
                ],
                "comparison": {
                    "value": independent.get("previous_value"),
                    "change_percent": independent.get("change_percent"),
                },
            },
            expected,
        ).values()
    )


@pytest.mark.parametrize(
    "fault", ["value", "date", "source", "records", "missing", "groups", "previous", "evidence"]
)
def test_shared_score_rejects_wrong_or_missing_answers(fault):
    case = DATA["agents"][3]
    result, selection = answer(case)
    assert scoring.score_answer(case, result, selection)["automatic_passed"]
    if fault == "value":
        result["metrics"][0]["value"] = "1401"
    elif fault == "date":
        result["metrics"][0]["filters"]["start"] = "2026-06-01"
    elif fault == "source":
        result["metrics"][0]["filename"] = "orders.csv"
    elif fault == "records":
        result["metrics"][0]["records"] = [1, 2, 3]
    elif fault == "missing":
        result["metrics"] = []
    elif fault == "groups":
        result["metrics"][0]["groups"][0]["value"] = "0"
    elif fault == "previous":
        result["metrics"][0]["comparison"]["value"] = "3400"
    else:
        result["evidence"] = []
    assert not scoring.score_answer(case, result, selection)["automatic_passed"]


def test_correct_extra_context_is_allowed_but_unselected_or_invented_values_fail():
    case = DATA["agents"][3]
    result, selection = answer(case)
    extra = {
        "filename": "heldout_orders.csv",
        "metric": "net_sales",
        "filters": {"start": "2026-06-01", "end": "2026-06-30"},
        "value": "2000.00",
        "records": [1, 2],
        "groups": [],
        "comparison": None,
    }
    result["metrics"].append(extra)
    assert scoring.score_answer(case, result, selection)["automatic_passed"]
    extra["value"] = "4000.00"
    assert not scoring.score_answer(case, result, selection)["automatic_passed"]
    extra["value"], extra["filename"] = "2000.00", "unselected.csv"
    assert not scoring.score_answer(case, result, selection)["automatic_passed"]


def test_retrieval_rejects_empty_positive_wrong_slice_and_scope_leak():
    case = DATA["retrieval"][6]
    file = FILES["h_feedback"]
    text = scoring.original_text(file, record=1)
    hit = {
        "filename": file.name,
        "page": None,
        "record": 1,
        "text": text,
        "start": 0,
        "end": len(text),
    }
    files = {file.name: file}
    assert scoring.score_retrieval(case, [hit], files)["passed"]
    assert not scoring.score_retrieval(case, [], files)["passed"]
    assert not scoring.score_retrieval(case, [{**hit, "end": len(text) - 1}], files)["passed"]
    assert not scoring.score_retrieval(case, [hit, {**hit, "filename": "feedback.csv"}], files)[
        "passed"
    ]
    negative = {**case, "targets": []}
    assert scoring.score_retrieval(negative, [], files)["passed"]
    assert not scoring.score_retrieval(negative, [hit], files)["passed"]


@pytest.mark.parametrize("asset,record", [("orders", 5), ("feedback", 1)])
def test_baseline_can_quote_exact_original_csv_record(asset, record):
    file = FILES[asset]
    row = file.read_text("utf-8-sig").splitlines()[record]
    item = {"filename": file.name, "page": None, "record": record, "quote": row}
    assert scoring.citation_valid(item, {file.name: file})
    assert not scoring.citation_valid({**item, "record": record + 1}, {file.name: file})


def test_baseline_csv_record_locator_handles_multiline_cells_without_using_physical_line(tmp_path):
    file = tmp_path / "feedback.csv"
    file.write_text('feedback_id,text\nF1,"第一行\n第二行"\nF2,另一条记录\n', encoding="utf-8")
    item = {"filename": file.name, "record": 1, "page": None, "quote": 'F1,"第一行\n第二行"'}
    assert scoring.citation_valid(item, {file.name: file})
    assert not scoring.citation_valid({**item, "record": 2}, {file.name: file})


def test_summary_keeps_failed_and_unrun_in_denominator():
    target = [{"filename": "a.csv"}]
    rows = [
        {"status": "completed", "targets": target, "checks": {"passed": True, "rank": 2}},
        {"status": "failed", "targets": target},
        {"status": "not_run", "targets": target},
    ]
    summary = scoring.summarize(rows)
    assert summary["total"] == 3 and summary["completed"] == 1
    assert summary["automatic_passed"] == 1
    assert summary["hit_at_5"] == pytest.approx(1 / 3)
    assert summary["mrr_at_5"] == pytest.approx(1 / 6)


def test_budget_counts_failed_attempts_across_new_outputs_and_reserves_baselines(tmp_path):
    file = tmp_path / "budget.json"
    budget = runner.Budget(file, 3)
    attempt = budget.reserve()
    budget.finish(attempt, error="ModelError")
    next_run = runner.Budget(file, 3)
    assert next_run.remaining == 2
    next_run.reserved = 1
    next_run.reserve()
    with pytest.raises(RuntimeError):
        next_run.reserve()
    next_run.reserved = 0
    next_run.reserve()
    with pytest.raises(RuntimeError):
        next_run.reserve()
    assert len(json.loads(file.read_text())["requests"]) == 3
    with pytest.raises(ValueError):
        runner.Budget(file, 65)


def test_safe_save_rejects_secret_before_replacing_existing_result(tmp_path):
    file = tmp_path / "result.json"
    file.write_text("previous", encoding="utf-8")
    with pytest.raises(RuntimeError):
        runner.save(file, {"value": "SECRET_FOR_TEST_ONLY"}, ["SECRET_FOR_TEST_ONLY"])
    assert file.read_text() == "previous"


def test_baseline_materials_share_scope_without_gold_values_or_other_files():
    case = DATA["agents"][5]
    documents = runner.materials(case, FILES)
    assert [d["filename"] for d in documents] == ["heldout_feedback.csv"]
    assert "expected_metrics" not in json.dumps(documents)
    assert not any(d["saved_order_mapping"] for d in documents)


def test_baseline_rejects_unrecognized_raw_response_fields_without_retry():
    class Model:
        calls = 0
        closed = False

        def __init__(self, config):
            pass

        async def complete(self, messages):
            Model.calls += 1
            return SimpleNamespace(
                message={"content": '{"raw_reasoning":"PRIVATE_REPLY_TEST_ONLY"}'},
                usage={"total_tokens": 9},
            )

        async def close(self):
            Model.closed = True

    with pytest.raises(ValueError):
        asyncio.run(runner.baseline(Model, SimpleNamespace(timeout=5), DATA["agents"][5], FILES))
    assert Model.calls == 1 and Model.closed


def test_live_batch_continues_after_both_arms_fail_and_saves_accepted_run_id(monkeypatch, tmp_path):
    async def failed_baseline(*args):
        raise ValueError("Untrusted response that must not be printed")

    monkeypatch.setattr(runner, "baseline", failed_baseline)
    monkeypatch.setattr(
        runner, "project", lambda *args: "/api/projects/00000000-0000-0000-0000-000000000001"
    )
    monkeypatch.setattr(runner, "upload", lambda *args, **kwargs: "source")
    monkeypatch.setattr(runner, "poll", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    client = SimpleNamespace(
        post=lambda *args, **kwargs: SimpleNamespace(
            status_code=202, json=lambda: {"id": "00000000-0000-0000-0000-000000000002"}
        )
    )
    outcome = {"agent": [], "baseline": []}
    snapshots = []
    runner.live(
        client,
        {"agents": DATA["agents"][:2]},
        FILES,
        outcome,
        lambda: snapshots.append(deepcopy(outcome)),
        runner.Budget(tmp_path / "budget.json"),
        None,
        None,
    )
    assert len(outcome["agent"]) == len(outcome["baseline"]) == 2
    assert all(row["status"] == "failed" for row in outcome["baseline"])
    assert all(row["status"] == "timeout" for row in outcome["agent"])
    assert any(row.get("run_id") for snapshot in snapshots for row in snapshot["agent"])


def test_dataset_counts_and_splits_are_explicit():
    assert [len(DATA[k]) for k in ("metrics", "retrieval", "agents")] == [12, 12, 6]
    for kind in ("metrics", "retrieval", "agents"):
        assert len({c["id"] for c in DATA[kind]}) == len(DATA[kind])
        assert {c["split"] for c in DATA[kind]} == {"development", "heldout"}
