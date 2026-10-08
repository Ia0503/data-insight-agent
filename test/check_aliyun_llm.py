"""手动运行一次百炼连通性检查；不进入默认回归，不启用 Agent。"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import httpx
from app.agent.model import ModelConfig, ModelError, parse_reply, resolve_config
from app.core.config import Settings
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "test" / "results" / "aliyun-smoke.json"
STATUS_HINTS = {
    400: "请求被拒绝，请核对模型 ID 及模型支持的参数。",
    401: "鉴权失败，请核对 API Key 是否有效，以及密钥与接口地域是否一致。",
    403: "访问被拒绝，请检查业务空间、模型权限、IP 限制和账户状态。",
    404: "地址或模型不可用，请核对 BASE_URL 和模型 ID。",
    429: "请求受限，请检查额度、账户状态或调用频率。",
}


def load_config() -> ModelConfig:
    if not (ROOT / ".env").is_file():
        raise ModelError("项目根目录缺少 .env。")
    # 明确使用本地文件中的百炼三项，不被旧的进程环境变量覆盖；不访问数据库。
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    settings = Settings(
        _env_file=None,
        database_url="",
        llm_provider="aliyun",
        llm_calls_enabled=False,
        llm_thinking_enabled=False,
        llm_aliyun_base_url=values.get("LLM_ALIYUN_BASE_URL") or "",
        llm_aliyun_model=values.get("LLM_ALIYUN_MODEL") or "",
        llm_aliyun_api_key=values.get("LLM_ALIYUN_API_KEY") or "",
    )
    config = resolve_config(settings, require_enabled=False)
    # 只检查 HTTP 请求头可传输性，不要求密钥具有某种前缀或固定长度。
    if any(not 33 <= ord(character) <= 126 for character in config.api_key):
        raise ModelError("API Key 含空白或非 ASCII 字符，请复制控制台的完整密钥值。")
    return config


async def probe(config: ModelConfig, *, transport=None) -> dict:
    result = {"success": False, "http_status": None}
    try:
        # 单次请求，无重试/重定向；禁用隐式代理，密钥仅发送到已校验的百炼地址。
        async with asyncio.timeout(60):
            async with (
                httpx.AsyncClient(
                    timeout=60,
                    follow_redirects=False,
                    trust_env=False,
                    transport=transport,
                ) as client,
                client.stream(
                    "POST",
                    config.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + config.api_key},
                    json={
                        "model": config.model,
                        "messages": [{"role": "user", "content": "请只回复 OK。"}],
                        "stream": False,
                        "enable_thinking": False,
                        "max_tokens": 64,
                    },
                ) as response,
            ):
                result["http_status"] = response.status_code
                if response.status_code != 200:
                    result["notice"] = STATUS_HINTS.get(
                        response.status_code,
                        "接口未返回成功状态；没有自动重发，也没有保存原始响应。",
                    )
                    return result
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1024 * 1024:
                        raise ModelError("响应超过检查上限，已停止读取。")
                reply = parse_reply(json.loads(content))
                answer = reply.message.get("content")
                if not isinstance(answer, str) or not answer.strip():
                    raise ModelError("接口返回成功状态，但没有可用的文本回答。")
                result.update(
                    success=True,
                    expected_reply=answer.strip() == "OK",
                    usage=reply.usage,
                    notice="鉴权及一次普通文本调用成功。",
                )
    except (TimeoutError, httpx.TimeoutException):
        result["notice"] = "请求超时；可用性尚未确认，没有自动重发。"
    except httpx.HTTPError:
        result["notice"] = "网络或 TLS 请求失败；可用性尚未确认，没有自动重发。"
    except ModelError as exc:
        result["notice"] = str(exc)
    except (ValueError, UnicodeError):
        result["notice"] = "接口响应无法解析，未保存原始响应。"
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    started = monotonic()
    result = {
        "provider": "aliyun",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result.update(asyncio.run(probe(load_config())))
    except ModelError as exc:
        result.update(success=False, http_status=None, notice=str(exc))
    except Exception:  # noqa: BLE001 - CLI 最外层防止底层异常携带密钥被打印
        # 配置/底层异常可能包含输入，不输出异常文本或 traceback。
        result.update(success=False, http_status=None, notice="检查未完成，请核对配置及运行环境。")
    result["elapsed_seconds"] = round(monotonic() - started, 2)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    try:
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(output + "\n", encoding="utf-8")
    except OSError:
        print("无法保存检查结果；以上输出仍为本次实际结果。")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
