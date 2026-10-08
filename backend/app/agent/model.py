import asyncio
import json
import logging
from dataclasses import dataclass, field
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from app.core.access import parse_hash
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

PROVIDERS = ("aliyun", "deepseek", "siliconflow", "volcengine", "zhipu", "moonshot", "custom")
HOSTS = {
    "deepseek": ("api.deepseek.com",),
    "siliconflow": ("api.siliconflow.cn", "api.siliconflow.com"),
    "volcengine": ("ark.cn-beijing.volces.com",),
    "zhipu": ("open.bigmodel.cn",),
    "moonshot": ("api.moonshot.cn", "api.moonshot.ai"),
}


class ModelError(Exception):
    """Only controlled text is allowed outside the model boundary."""


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    thinking: bool = False
    timeout: int = 60


def resolve_config(settings: Settings | None = None, *, require_enabled=True) -> ModelConfig:
    settings = settings or get_settings()
    if require_enabled and not settings.llm_calls_enabled:
        raise ModelError("真实模型调用尚未启用；填写配置并完成接入确认后再开启。")
    provider = settings.llm_provider.strip().lower()
    if provider not in PROVIDERS:
        raise ModelError("请在本地 .env 填写有效的 LLM_PROVIDER。")
    base = getattr(settings, f"llm_{provider}_base_url").strip().rstrip("/")
    model = getattr(settings, f"llm_{provider}_model").strip()
    key = getattr(settings, f"llm_{provider}_api_key").get_secret_value().strip()
    if not base or not model or not key or any("YOUR_" in value for value in (base, model, key)):
        raise ModelError("所选平台的 BASE_URL、MODEL、API_KEY 尚未完整填写。")
    try:
        url = urlsplit(base)
        port = url.port
    except ValueError as exc:
        raise ModelError("BASE_URL 的域名或端口格式无效。") from exc
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or "{" in base
        or "}" in base
        or base.endswith("/chat/completions")
        or len(model) > 120
        or (provider != "custom" and port not in (None, 443))
        or any(character.isspace() for character in base)
    ):
        raise ModelError(
            "BASE_URL 必须是有效 HTTPS 基础地址，不含账号、查询参数或 chat/completions。"
        )
    if provider == "aliyun":
        valid_host = url.hostname in {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "cn-hongkong.dashscope.aliyuncs.com",
        } or url.hostname.endswith(".maas.aliyuncs.com")
        if not valid_host or "coding" in base or "token-plan" in base:
            raise ModelError("百炼配置需要普通模型 API 地址，不能使用 Coding/Token Plan 地址。")
    elif provider != "custom" and url.hostname not in HOSTS[provider]:
        raise ModelError("接口域名与所选平台不匹配；自建网关请使用 custom 并确认地址。")
    if settings.llm_thinking_enabled and provider not in ("aliyun", "deepseek"):
        raise ModelError("此平台暂未验证思考模式参数，请关闭 LLM_THINKING_ENABLED。")
    return ModelConfig(
        provider, model, base, key, settings.llm_thinking_enabled, settings.llm_timeout_seconds
    )


def configuration_status():
    settings = get_settings()
    access_configured = (
        not settings.analysis_password_required
        or parse_hash(settings.analysis_password_hash.get_secret_value()) is not None
    )
    protection = {
        "password_required": settings.analysis_password_required,
        "access_configured": access_configured,
    }
    try:
        config = resolve_config(require_enabled=False)
        return {
            "configured": True,
            "enabled": settings.llm_calls_enabled and access_configured,
            **protection,
            "provider": config.provider,
            "model": config.model,
            "notice": "已填写本地配置，尚不代表已通过真实接入测试。"
            if access_configured
            else "调用密码未配置，请先运行本地密码配置脚本。",
        }
    except ModelError as exc:
        return {
            "configured": False,
            "enabled": settings.llm_calls_enabled,
            **protection,
            "provider": settings.llm_provider if settings.llm_provider in PROVIDERS else "",
            "model": "",
            "notice": str(exc),
        }


@dataclass
class Reply:
    message: dict
    usage: dict


def safe_usage(value: dict | None) -> dict:
    """Keep only consistent provider counters; invalid accounting must not discard valid text."""
    if not isinstance(value, dict):
        return {}
    usage = {
        key: value[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if key in value
    }
    if any(type(count) is not int or not 0 <= count < 100_000_000 for count in usage.values()):
        return {}
    if "total_tokens" in usage and len(usage) > 1:
        if (
            "prompt_tokens" not in usage
            or "completion_tokens" not in usage
            or usage["prompt_tokens"] + usage["completion_tokens"] != usage["total_tokens"]
        ):
            return {}
    return usage


def parse_reply(payload: dict) -> Reply:
    try:
        message = payload["choices"][0]["message"]
        if message.get("role") != "assistant":
            raise ValueError
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 12:
            raise ValueError
        ids = set()
        for call in calls:
            function = call["function"]
            if (
                call["type"] != "function"
                or not isinstance(call["id"], str)
                or not 0 < len(call["id"]) <= 128
                or call["id"] in ids
                or not isinstance(function["name"], str)
                or not isinstance(function["arguments"], str)
                or len(function["arguments"]) > 12000
            ):
                raise ValueError
            ids.add(call["id"])
        # 思考模式的工具续接需要保留此字段；仅在任务内存中流转，不持久化或展示。
        safe = {"role": "assistant", "content": content}
        if calls:
            safe["tool_calls"] = calls
        if "reasoning_content" in message:
            if not isinstance(message["reasoning_content"], (str, type(None))):
                raise ValueError
            safe["reasoning_content"] = message["reasoning_content"]
        return Reply(safe, safe_usage(payload.get("usage")))
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise ModelError("模型返回格式无效，任务停止；没有自动重发请求。") from exc


class ChatModel:
    def __init__(self, config: ModelConfig):
        self.config = config
        # 队列在执行前绑定任务；显式命令行接入探测不属于网页任务。
        self.run_id = None
        self.client = httpx.AsyncClient(
            timeout=config.timeout, follow_redirects=False, trust_env=False
        )

    async def close(self):
        await self.client.aclose()

    async def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "max_tokens": 4096,
        }
        if self.config.provider == "aliyun":
            body["enable_thinking"] = self.config.thinking
        elif self.config.provider == "deepseek":
            body["thinking"] = {"type": "enabled" if self.config.thinking else "disabled"}
        if tools:
            body.update(tools=tools, tool_choice="auto")
        if len(json.dumps(body, ensure_ascii=False)) > 180_000:
            raise ModelError("模型上下文超过本项目预算，请减少数据源或缩小工具输出。")
        reservation = None
        usage = None
        started = perf_counter()
        if self.run_id is not None:
            from app.services.llm_budget import request_bound, reserve_tokens

            reservation = await asyncio.to_thread(reserve_tokens, self.run_id, request_bound(body))
        try:
            # 不跟随重定向，避免携带密钥跳到其他域名；限制响应体并且不回显上游错误正文。
            async with self.client.stream(
                "POST",
                self.config.base_url + "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            ) as response:
                if response.status_code != 200:
                    notices = {
                        401: "模型鉴权失败，请核对平台、地域和密钥。",
                        403: "模型访问被拒绝，请核对权限。",
                        429: "模型配额或限流，请稍后手动重试。",
                    }
                    raise ModelError(
                        notices.get(
                            response.status_code,
                            "模型服务请求失败，请核对地址、模型和参数；没有自动重试。",
                        )
                    )
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > 1_048_576:
                        raise ModelError("模型响应超过大小限制，任务停止。")
                payload = json.loads(data)
                # 即使业务响应格式无效，可靠的用量仍按服务商数据核销。
                usage = payload.get("usage") if isinstance(payload, dict) else None
                return parse_reply(payload)
        except httpx.TimeoutException as exc:
            raise ModelError("模型请求超时，服务商可能已计费；没有自动重试。") from exc
        except httpx.HTTPError as exc:
            raise ModelError("无法连接模型服务；没有自动重试。") from exc
        except (ValueError, UnicodeError) as exc:
            raise ModelError("模型返回无效 JSON，任务停止。") from exc
        finally:
            if reservation is not None:
                from app.services.llm_budget import QuotaError, settle_tokens

                try:
                    await asyncio.to_thread(settle_tokens, reservation, usage)
                except QuotaError:
                    raise
                except Exception as exc:
                    logger.error(
                        "event=model_budget_settlement_failed run_id=%s type=%s",
                        self.run_id,
                        type(exc).__name__,
                    )
                    raise ModelError(
                        "模型用量记录暂不可用，任务已停止；保留请求额度，不自动重发。"
                    ) from exc
            logger.info(
                "event=model_request_finished run_id=%s duration_ms=%.1f",
                self.run_id,
                (perf_counter() - started) * 1000,
            )


def create_model(config: ModelConfig):
    return ChatModel(config)
