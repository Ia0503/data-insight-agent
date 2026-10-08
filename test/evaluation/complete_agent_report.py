"""手动补测已有虚构工具结果的报告阶段，最多两次计费请求，不改原任务终态。"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from run_agent_live import OUTPUT, ROOT, require, verify_case


def inspect_reply(reply, results):
    """只保存约束类型和已知字段路径，不保存模型原文、思考或无效输入值。"""
    from app.agent.report import ReportError, schema_diagnostics, validate_report
    from app.schemas.agent import ReportDraft
    from pydantic import ValidationError

    content = reply.message.get("content") or ""
    if content.startswith("```json\n") and content.endswith("\n```"):
        content = content[8:-4]
    if reply.message.get("tool_calls"):
        return {"schema_valid": False, "reason": "unexpected_tool_calls"}
    try:
        draft = ReportDraft.model_validate_json(content)
    except ValidationError as exc:
        return {
            "schema_valid": False,
            "errors": schema_diagnostics(exc, ReportDraft),
        }
    texts = {
        "title": draft.title,
        "summary": draft.summary,
        **{
            f"findings[{i}].text": item.text
            for i, item in enumerate(draft.findings)
            if hasattr(item, "text")
        },
        **{f"recommendations[{i}]": text for i, text in enumerate(draft.recommendations)},
        **{f"limitations[{i}]": text for i, text in enumerate(draft.limitations)},
        **{f"chart_specs[{i}].title": item.title for i, item in enumerate(draft.chart_specs)},
    }
    diagnostic = {"schema_valid": True}
    try:
        validate_report(draft, results)
        diagnostic["constraints_valid"] = True
    except ReportError as exc:
        diagnostic.update(
            constraints_valid=False,
            constraint_error=str(exc),
            digit_fields=[path for path, text in texts.items() if re.search(r"\d", text)],
            result_ids_in_narrative=sorted(
                {
                    item["id"]
                    for item in results
                    if any(
                        re.search(r"(?<![A-Za-z0-9])" + re.escape(item["id"]) + r"(?![0-9])", text)
                        for text in texts.values()
                    )
                }
            ),
        )
    return diagnostic


async def generate(run, results, config):
    from app.agent.graph import AgentWorkflow
    from app.agent.model import ChatModel, ModelError
    from app.agent.tools import ToolExecutor
    from langsmith import tracing_context

    diagnostics = {"http_statuses": [], "replies": []}

    async def response_status(response):
        diagnostics["http_statuses"].append(response.status_code)

    class BoundedModel(ChatModel):
        requests = 0

        async def complete(self, messages, tools=None):
            if self.requests >= 2:
                raise ModelError("报告补测达到两次请求上限，没有继续请求。")
            self.requests += 1
            reply = await super().complete(messages, tools)
            diagnostics["replies"].append(inspect_reply(reply, results))
            return reply

    events = []

    async def emit(kind, message, **changes):
        events.append({"kind": kind, "message": message, **changes})

    model = BoundedModel(config)
    model.client.event_hooks["response"] = [response_status]
    task = SimpleNamespace(
        project_id=UUID(run["project_id"]), question=run["question"], snapshot=run["snapshot"]
    )
    workflow = AgentWorkflow(task, model, ToolExecutor(task.project_id, task.snapshot), emit)
    try:
        with tracing_context(enabled=False):
            async with asyncio.timeout(150):
                await workflow.check({})
                state = {"results": results}
                state.update(await workflow.report(state))
                state.update(await workflow.validate(state))
                return {
                    "status": "succeeded",
                    "report": state["report"],
                    "actual_model_requests": model.requests,
                    "usage": workflow.usage,
                    "events": events,
                    "diagnostics": diagnostics,
                }
    except (ModelError, TimeoutError) as exc:
        return {
            "status": "failed",
            "error": str(exc) if isinstance(exc, ModelError) else "报告补测超过总期限。",
            "actual_model_requests": model.requests,
            "usage": workflow.usage,
            "events": events,
            "diagnostics": diagnostics,
        }
    finally:
        await model.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--case", choices=("injection", "normal", "missing"), default="injection")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url

    values = dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False)
    url = values.get("TEST_DATABASE_URL")
    require(url and make_url(url).database == "newai_test", "只允许 newai_test。")
    os.environ.update(
        DATABASE_URL=url,
        UPLOAD_DIR=str(ROOT / "test/results/uploads"),
        LLM_CALLS_ENABLED="false",
    )
    from check_aliyun_llm import load_config

    config = load_config()
    original = json.loads((OUTPUT / f"results-{args.case}.json").read_text("utf-8"))["cases"][0]
    require(original["case"] == args.case, "补测仅接受对应的已保存虚构场景。")
    completion = asyncio.run(generate(original["run"], original["results"], config))
    from app.main import app
    from fastapi.testclient import TestClient

    checks = {}
    if completion["status"] == "succeeded":
        with TestClient(app) as client:
            try:
                checks = verify_case(
                    args.case,
                    {**original["run"], "status": "succeeded", "report": completion["report"]},
                    original["results"],
                    client,
                    "/api/projects/" + original["project_id"],
                )
            except RuntimeError as exc:
                completion.update(status="failed", check_error=str(exc))
    completion.update(
        checked_at=datetime.now(timezone.utc).isoformat(),
        checks=checks,
        original_run_id=original["run"]["id"],
        original_task_status=original["run"]["status"],
        mode="report_only_from_existing_real_tool_results",
    )
    encoded = json.dumps(completion, ensure_ascii=False, indent=2)
    require(config.api_key not in encoded, "输出敏感内容校验失败。")
    (OUTPUT / f"{args.case}-report-completion.json").write_text(encoded, "utf-8")
    print(
        json.dumps(
            {key: value for key, value in completion.items() if key not in ("report", "events")},
            ensure_ascii=False,
        )
    )
    return 0 if completion["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - 不打印可能携带敏感输入的底层异常
        print(json.dumps({"passed": False, "exception_type": type(exc).__name__}))
        sys.exit(1)
