import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from app.agent.model import ModelConfig

spec = importlib.util.spec_from_file_location(
    "check_aliyun_llm", Path(__file__).resolve().parents[1] / "check_aliyun_llm.py"
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.mark.parametrize("status", [200, 401, 307])
def test_probe_single_request_and_secret_safe_result(status):
    key = "test-only-credential-without-sk-prefix"
    config = ModelConfig("aliyun", "test-model", "https://dashscope.aliyuncs.com/v1", key)
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["max_tokens"] == 64
        assert body["enable_thinking"] is False
        assert request.headers["authorization"] == "Bearer " + key
        payload = {
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }
        if status != 200:
            payload = {"error": {"message": key}}
        return httpx.Response(status, json=payload, headers={"Location": "https://wrong.invalid"})

    result = asyncio.run(script.probe(config, transport=httpx.MockTransport(handler)))
    assert len(requests) == 1
    assert result["success"] is (status == 200)
    assert result["http_status"] == status
    assert key not in json.dumps(result)
    if status == 200:
        assert result["expected_reply"] is True
        assert result["usage"]["total_tokens"] == 6


def test_probe_network_error_does_not_leak_or_retry():
    calls = []
    config = ModelConfig("aliyun", "test-model", "https://dashscope.aliyuncs.com/v1", "test-key")

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("secret error: test-key", request=request)

    result = asyncio.run(script.probe(config, transport=httpx.MockTransport(handler)))
    assert len(calls) == 1
    assert result["success"] is False
    assert "test-key" not in json.dumps(result)
