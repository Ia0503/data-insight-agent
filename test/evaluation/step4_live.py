"""步骤四真实网页联调：显式授权运行隔离服务；验证模式只读，不调用模型。"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "test"))
sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--max-calls", type=int, choices=range(1, 49), default=32)
    args = parser.parse_args()
    if not args.run and not args.verify:
        parser.print_help()
        return 0
    output = Path(os.environ["STEP4_LIVE_DIR"]).resolve()
    if not output.is_relative_to(ROOT / "test/results"):
        raise RuntimeError("联调产物必须位于 test/results 内。")
    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    url = values.get("TEST_DATABASE_URL")
    if not url or make_url(url).database != "newai_test":
        raise RuntimeError("联调只允许显式 newai_test 数据库。")
    os.environ.update(
        DATABASE_URL=url,
        UPLOAD_DIR=str(ROOT / "test/results/uploads"),
        LLM_CALLS_ENABLED="false" if args.verify else "true",
        LLM_THINKING_ENABLED="false",
    )
    from run_agent_live import QUESTIONS, verify_case

    cases = list(QUESTIONS)
    selected_case = os.environ.get("STEP4_LIVE_CASE")
    if selected_case:
        if selected_case not in QUESTIONS:
            raise RuntimeError("未识别的联调场景。")
        cases = [selected_case]

    if args.verify:
        import httpx

        # 不启动应用生命周期，防止只读核对重置其他服务的活动任务。
        from app.core.database import SessionLocal
        from app.models.agent import AgentRun

        outcomes = []
        with httpx.Client(base_url="http://127.0.0.1:8002", timeout=30, trust_env=False) as client:
            for case in cases:
                artifact = output / f"{case}.json"
                if not artifact.exists():
                    outcomes.append({"case": case, "passed": False, "error": "网页未完成此用例。"})
                    continue
                item = json.loads(artifact.read_text("utf-8"))
                with SessionLocal() as db:
                    results = db.get(AgentRun, UUID(item["run"]["id"])).results
                item["results"] = results
                try:
                    item["checks"] = verify_case(
                        case, item["run"], results, client, "/api/projects/" + item["project_id"]
                    )
                    item["passed"] = True
                except RuntimeError as exc:
                    item.update(passed=False, check_error=str(exc))
                outcomes.append(item)
        outcome = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "cases": outcomes,
            "model_requests": json.loads((output / "model-requests.json").read_text("utf-8")),
            "passed": all(item["passed"] for item in outcomes),
            "limits": "人工虚构小样本，未验证其他平台、思考模式或泛化准确率。",
        }
        encoded = json.dumps(outcome, ensure_ascii=False, indent=2)
        secrets = [value for key, value in values.items() if "API_KEY" in key and value]
        if any(secret in encoded for secret in secrets):
            raise RuntimeError("产物敏感值检查失败。")
        result_name = f"verified-{selected_case}.json" if selected_case else "verified.json"
        (output / result_name).write_text(encoded, "utf-8")
        for item in outcomes:
            print(
                json.dumps(
                    {
                        "case": item["case"],
                        "passed": item["passed"],
                        "checks": item.get("checks"),
                        "check_error": item.get("check_error"),
                        "usage": item.get("run", {}).get("usage"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return 0 if outcome["passed"] else 1

    from check_aliyun_llm import load_config

    config = load_config()
    os.environ.update(
        LLM_PROVIDER="aliyun",
        LLM_ALIYUN_BASE_URL=config.base_url,
        LLM_ALIYUN_MODEL=config.model,
        LLM_ALIYUN_API_KEY=config.api_key,
    )
    from alembic import command
    from alembic.config import Config
    from app.agent import model as models
    from complete_agent_report import inspect_reply

    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists():
        raise RuntimeError("此目录已有联调，使用新目录以保留原始结果。")
    calls = {"total": 0, "limit": args.max_calls, "per_run_limit": 16}
    diagnostics = []

    class BoundedModel(models.ChatModel):
        def __init__(self, value):
            super().__init__(value)
            self.attempts = 0

        async def complete(self, messages, tools=None):
            if self.attempts >= 16 or calls["total"] >= args.max_calls:
                raise models.ModelError("本轮网页联调达到调用上限，没有继续请求。")
            self.attempts += 1
            calls["total"] += 1
            (output / "model-requests.json").write_text(json.dumps(calls), "utf-8")
            reply = await super().complete(messages, tools)
            # 只识别本应用报告提示中的受控字段，保存诊断，不保存模型回复或思考。
            for message in messages:
                try:
                    prompt = json.loads(message.get("content") or "")
                except (TypeError, ValueError):
                    continue
                if isinstance(prompt, dict) and "fact_catalog" in prompt and "results" in prompt:
                    diagnostics.append(
                        {"request": calls["total"], **inspect_reply(reply, prompt["results"])}
                    )
                    (output / "report-diagnostics.json").write_text(
                        json.dumps(diagnostics, ensure_ascii=False, indent=2), "utf-8"
                    )
                    break
            return reply

    models.create_model = BoundedModel
    command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    (output / "model-requests.json").write_text(json.dumps(calls), "utf-8")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "questions": QUESTIONS,
                "cases": cases,
                "provider": config.provider,
                "model": config.model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    handler = logging.FileHandler(output / "events.log", encoding="utf-8")
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logging.getLogger("app").addHandler(handler)
    import uvicorn
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=8002, access_log=False)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - 不输出可能携带凭据的底层异常内容
        print(json.dumps({"passed": False, "exception_type": type(exc).__name__}))
        sys.exit(1)
