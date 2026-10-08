import asyncio
import json

import httpx
import pytest
from app.agent.model import ChatModel, ModelConfig, ModelError, parse_reply, resolve_config
from app.agent.report import ReportError, validate_report
from app.core.config import Settings
from app.schemas.agent import ReportDraft


def settings(**values):
    return Settings(
        _env_file=None,
        database_url="postgresql://test-only",
        llm_calls_enabled=True,
        llm_provider="deepseek",
        llm_deepseek_base_url="https://api.deepseek.com",
        llm_deepseek_model="test-model",
        llm_deepseek_api_key="test-only-token",
        **values,
    )


def test_config_gate_and_platform_credentials():
    value = settings()
    value.llm_calls_enabled = False
    with pytest.raises(ModelError, match="尚未启用"):
        resolve_config(value)
    value.llm_calls_enabled = True
    assert "test-only-token" not in repr(resolve_config(value))
    value.llm_deepseek_base_url = "https://wrong.invalid"
    with pytest.raises(ModelError, match="域名"):
        resolve_config(value)
    value.llm_deepseek_base_url = "https://api.deepseek.com"
    value.llm_deepseek_api_key = __import__("pydantic").SecretStr("YOUR_API_KEY_HERE")
    with pytest.raises(ModelError, match="完整填写"):
        resolve_config(value)


@pytest.mark.parametrize("provider", ["aliyun", "deepseek"])
def test_platform_parameters_and_reasoning_roundtrip(provider):
    captured = []

    async def run():
        model = ChatModel(
            ModelConfig(
                provider,
                "test-model",
                "https://example.invalid/v1",
                "test-only-token",
                thinking=True,
            )
        )
        await model.client.aclose()

        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "private-test-reasoning",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "get_schema", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                },
            )

        model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reply = await model.complete(
            [{"role": "user", "content": "test"}],
            [{"type": "function", "function": {"name": "get_schema"}}],
        )
        assert reply.message["reasoning_content"] == "private-test-reasoning"
        assert reply.usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        await model.complete(
            [reply.message, {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
        )
        await model.close()

    asyncio.run(run())
    assert (
        captured[0]["enable_thinking"] is True
        if provider == "aliyun"
        else captured[0]["thinking"] == {"type": "enabled"}
    )
    assert captured[1]["messages"][0]["reasoning_content"] == "private-test-reasoning"


@pytest.mark.parametrize("failure", ["auth", "timeout", "invalid", "oversize", "redirect"])
def test_http_failures_are_bounded_without_secret_or_retry(failure):
    calls = []

    async def run():
        model = ChatModel(
            ModelConfig("custom", "test-model", "https://example.invalid", "test-only-token")
        )
        await model.client.aclose()

        def handler(request):
            calls.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("test-only-token secret payload", request=request)
            if failure == "auth":
                return httpx.Response(401, text="test-only-token secret payload")
            if failure == "redirect":
                return httpx.Response(302, headers={"location": "https://elsewhere.invalid"})
            return httpx.Response(200, content=b"x" * (1_048_577 if failure == "oversize" else 3))

        model.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )
        with pytest.raises(ModelError) as caught:
            await model.complete([])
        assert "test-only-token" not in str(caught.value) and "payload" not in str(caught.value)
        await model.close()

    asyncio.run(run())
    assert len(calls) == 1


def test_reply_rejects_ambiguous_calls_and_fake_usage():
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": True, "total_tokens": -1, "completion_tokens": 4},
    }
    assert parse_reply(payload).usage == {}
    payload["choices"][0]["message"]["tool_calls"] = [
        {"id": "same", "type": "function", "function": {"name": "get_schema", "arguments": "{}"}}
    ] * 2
    with pytest.raises(ModelError):
        parse_reply(payload)


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 7},
        {"prompt_tokens": 5, "completion_tokens": True, "total_tokens": 6},
        {"prompt_tokens": 5, "total_tokens": 7},
        ["malformed-usage"],
    ],
)
def test_reply_keeps_valid_text_but_rejects_inconsistent_usage(usage):
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "valid-test-reply"}}],
        "usage": usage,
    }
    reply = parse_reply(payload)
    assert reply.message["content"] == "valid-test-reply"
    assert reply.usage == {}


@pytest.mark.parametrize(
    "usage",
    [
        {"total_tokens": 7},
        {"prompt_tokens": 5},
        {"completion_tokens": 2},
        {"prompt_tokens": 5, "completion_tokens": 2},
    ],
)
def test_valid_partial_usage_is_preserved_without_inventing_missing_counters(usage):
    reply = parse_reply(
        {"choices": [{"message": {"role": "assistant", "content": "test"}}], "usage": usage}
    )
    assert reply.usage == usage


def test_report_sources_and_values_come_only_from_tool_results():
    draft = ReportDraft(
        title="报告",
        summary="",
        findings=[{"kind": "fact", "fact_id": "R1:metric"}],
        limitations=["无法证明因果关系。"],
        metric_ids=["R1"],
        chart_specs=[{"title": "地区比较", "result_id": "R1"}],
    )
    results = [
        {
            "id": "R1",
            "tool": "calculate_metric",
            "data": {
                "metric": "net_sales",
                "filters": {"start": "2026-09-01", "end": "2026-09-30"},
                "value": "76000.00",
                "unit": "元",
                "evidence": {"source_id": "source", "filename": "orders.csv"},
                "groups": [{"group": "华东", "value": "52000.00"}],
            },
        }
    ]
    report = validate_report(draft, results)
    assert report["metrics"][0]["value"] == "76000.00"
    assert report["chart_specs"][0]["values"] == ["52000.00"]
    assert "2026-09-01至2026-09-30" in report["chart_specs"][0]["title"]
    draft.title = "销售额为999999元"
    with pytest.raises(ReportError, match="数字"):
        validate_report(draft, results)
    draft.title = "报告"
    draft.findings[0].fact_id = "invented"
    with pytest.raises(ReportError, match="未匹配"):
        validate_report(draft, results)
