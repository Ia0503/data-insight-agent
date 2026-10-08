import asyncio
import json
from types import SimpleNamespace

import pytest
from app.agent.graph import AgentWorkflow
from app.agent.model import ModelError, Reply
from app.schemas.agent import Plan
from app.schemas.analysis import IndexInput


def test_index_column_identifiers_preserve_whitespace():
    value = IndexInput(text_field=" text ", record_id_field=" id ")
    assert value.text_field == " text " and value.record_id_field == " id "


@pytest.mark.parametrize("already_repaired", [False, True])
def test_exhausted_schema_repair_retains_safe_field_diagnostics(already_repaired):
    class InvalidModel:
        config = SimpleNamespace(timeout=1)
        calls = 0

        async def complete(self, messages, tools=None):
            self.calls += 1
            content = json.dumps({"steps": [], "test-private-key": "test-private-value"})
            return Reply({"content": content}, {})

    async def emit(*args, **kwargs):
        pass

    model = InvalidModel()
    workflow = AgentWorkflow(SimpleNamespace(), model, None, emit)
    workflow.repairs = int(already_repaired)
    with pytest.raises(ModelError) as caught:
        asyncio.run(workflow.structured([], Plan))
    detail = str(caught.value)
    assert "too_short" in detail and "steps" in detail and "unknown_field" in detail
    assert "test-private" not in detail
    assert model.calls == (1 if already_repaired else 2)


def test_exhausted_report_repair_retains_constraint_without_model_text():
    workflow = AgentWorkflow(SimpleNamespace(), None, None, None)
    workflow.repairs = 1
    state = {
        "results": [],
        "draft": {
            "title": "test-private-value R1",
            "summary": "",
            "findings": [{"kind": "unknown", "text": "未知", "evidence_ids": []}],
            "limitations": ["资料有限。"],
        },
    }
    with pytest.raises(ModelError) as caught:
        asyncio.run(workflow.validate(state))
    assert "title 含数字" in str(caught.value)
    assert "test-private-value" not in str(caught.value)
    assert workflow.repairs == 1 and workflow.model_calls == 0


def test_report_repair_collects_structure_and_reference_errors_together():
    class RepairModel:
        config = SimpleNamespace(timeout=1)
        calls = 0

        async def complete(self, messages, tools=None):
            self.calls += 1
            draft = {
                "title": "" if self.calls == 1 else "待补充资料",
                "findings": [{"kind": "unknown", "text": "缺少资料。", "evidence_ids": []}],
                "limitations": ["无法确认。"],
            }
            if self.calls == 1:
                draft["findings"][0]["evidence_ids"] = ["test-private-invalid-reference"]
            else:
                repair = json.loads(messages[-1]["content"].split("（无输入值）：")[1])
                diagnostic = repair["issues"]
                assert repair["guidance"]
                assert any(issue["path"] == ["title"] for issue in diagnostic)
                assert {
                    "type": "unavailable_reference",
                    "path": ["findings", 0, "evidence_ids", 0],
                } in diagnostic
                assert "test-private" not in messages[-1]["content"]
                assert messages[-2]["role"] == "assistant"
            return Reply({"content": json.dumps(draft)}, {})

    async def emit(*args, **kwargs):
        pass

    model = RepairModel()
    workflow = AgentWorkflow(SimpleNamespace(question="虚构问题"), model, None, emit)
    result = asyncio.run(workflow.report({"results": []}))
    assert result["draft"]["title"] == "待补充资料"
    assert workflow.repairs == 1 and model.calls == 2


@pytest.mark.parametrize("bad_title,bad_reference", [(True, False), (False, True)])
@pytest.mark.parametrize("already_repaired", [False, True])
def test_report_constraints_share_the_same_single_repair(
    bad_title, bad_reference, already_repaired
):
    class InvalidModel:
        config = SimpleNamespace(timeout=1)
        calls = 0

        async def complete(self, messages, tools=None):
            self.calls += 1
            return Reply(
                {
                    "content": json.dumps(
                        {
                            "title": "test-private-title R1" if bad_title else "待补充资料",
                            "findings": [
                                {
                                    "kind": "unknown",
                                    "text": "未知。",
                                    "evidence_ids": ["test-private-id"] if bad_reference else [],
                                }
                            ],
                            "limitations": ["资料不足。"],
                        }
                    )
                },
                {},
            )

    async def emit(*args, **kwargs):
        pass

    model = InvalidModel()
    workflow = AgentWorkflow(SimpleNamespace(question="虚构问题"), model, None, emit)
    workflow.repairs = int(already_repaired)
    with pytest.raises(ModelError) as caught:
        asyncio.run(workflow.report({"results": []}))
    assert "test-private" not in str(caught.value)
    assert (
        "title 含数字" in str(caught.value)
        if bad_title
        else "unavailable_reference" in str(caught.value)
    )
    assert workflow.repairs == 1 and workflow.model_calls == model.calls == (
        1 if already_repaired else 2
    )
