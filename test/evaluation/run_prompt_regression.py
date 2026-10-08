"""单轮提示词真实回归；只用 newai_test，保留旧评测，--run 显式允许计费。"""

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "test"))

from evaluation.run_batch import (  # noqa: E402
    CASES,
    FREEZE,
    RESULTS,
    agent_projection,
    checked,
    citation_valid,
    inputs,
    poll,
    prepare_settings,
    project,
    save,
    score_answer,
    upload,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=48)
    parser.add_argument(
        "--cases", nargs="+", help="只运行指定用例；分母仅为选中项，不覆盖全轮结果。"
    )
    args = parser.parse_args()
    if not args.run or not 1 <= args.max_calls <= 64:
        parser.error("Requires --run and a request limit within 1..64.")
    output = args.output.resolve()
    if (
        not output.is_relative_to(RESULTS.resolve())
        or output == RESULTS.resolve()
        or output.exists()
    ):
        raise RuntimeError("Requires a fresh output directory inside test/results.")
    # 仅本进程的隔离测试环境；保留原模型、参数、小时限额及本地配置文件。
    os.environ["ANALYSIS_PASSWORD_REQUIRED"] = "false"
    settings, config = prepare_settings(True)
    data = json.loads(CASES.read_text("utf-8"))
    files, hashes = inputs(data)
    selected_cases = [case for case in data["agents"] if not args.cases or case["id"] in args.cases]
    if not selected_cases or (
        args.cases and set(args.cases) != {case["id"] for case in selected_cases}
    ):
        raise RuntimeError("Unknown or empty case selection.")
    frozen = json.loads(FREEZE.read_text("utf-8"))
    if hashes != frozen["files"]:
        raise RuntimeError("Original evaluation inputs changed.")
    model_config = {
        "provider": config.provider,
        "model": config.model,
        "thinking": config.thinking,
        "timeout_seconds": config.timeout,
    }
    if model_config != frozen["llm"]:
        raise RuntimeError("Keep the original model and parameters for this regression.")
    output.mkdir(parents=True)
    outcome = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "prompt-regression",
        "notice": "Previously consumed cases; not an unseen or general accuracy benchmark. No new baseline calls.",
        "model": model_config,
        "selected_case_ids": [case["id"] for case in selected_cases],
        "files": hashes,
        "code": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in (
                "backend/app/agent/prompts.py",
                "backend/app/agent/graph.py",
                "backend/app/agent/tools.py",
                "backend/app/schemas/agent.py",
                "test/evaluation/run_prompt_regression.py",
                "test/evaluation/batch_scoring.py",
            )
        },
        "requests": [],
        "agent": [],
        "max_calls": args.max_calls,
    }
    secrets = (config.api_key, settings.database_url.get_secret_value())
    lock = threading.Lock()

    def persist():
        save(output / "results.json", outcome, secrets)

    from alembic import command
    from alembic.config import Config
    from app.agent import model as models
    from app.core.database import SessionLocal
    from app.models.agent import AgentRun
    from evaluation.run_agent_live import verify_generated_facts
    from sqlalchemy import select

    command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    with SessionLocal() as db:
        if db.scalar(select(AgentRun.id).where(AgentRun.status.in_(["queued", "running"]))):
            raise RuntimeError("Test database has active tasks; refuse another lifespan.")
    current = {"case": "setup"}

    class BoundedModel(models.ChatModel):
        async def complete(self, messages, tools=None):
            with lock:
                if len(outcome["requests"]) >= args.max_calls:
                    raise models.ModelError("提示词回归请求预算已耗尽，没有继续调用。")
                item = {
                    "case": current["case"],
                    "status": "attempted",
                    "usage": {},
                    "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "input_characters": sum(
                        len(json.dumps(message, ensure_ascii=False)) for message in messages
                    ),
                }
                outcome["requests"].append(item)
                persist()  # 发送前落盘；中断也保留已尝试请求，不自动重发。
            tick = time.monotonic()
            try:
                reply = await super().complete(messages, tools)
                item.update(status="returned", usage=reply.usage)
                return reply
            except BaseException as exc:
                item.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                item["elapsed_seconds"] = round(time.monotonic() - tick, 3)
                with lock:
                    persist()

    models.create_model = BoundedModel
    from app.main import app
    from fastapi.testclient import TestClient

    logger = logging.getLogger("app")
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(output / "events.log", encoding="utf-8"))
    logger.propagate = False
    persist()
    with TestClient(app) as client:
        for case in selected_cases:
            tick = time.monotonic()
            row = {
                "id": case["id"],
                "split": "previously_seen",
                "status": "not_run",
                "manual_status": "pending",
                "manual_checks": case["manual_checks"],
            }
            outcome["agent"].append(row)
            current["case"] = case["id"]
            begin = len(outcome["requests"])
            try:
                if args.max_calls - begin < 4:
                    raise RuntimeError("Insufficient requests for another task; no submission.")
                path = project(client, "提示词回归·" + case["id"])
                ids = [
                    upload(
                        client,
                        path,
                        files[asset],
                        indexed=asset not in {"orders", "h_orders", "h_summary"},
                    )
                    for asset in case["scope"]
                ]
                run = checked(
                    client.post(
                        path + "/agent/runs",
                        json={
                            "question": case["question"],
                            "source_ids": ids,
                            "request_id": str(uuid4()),
                        },
                    ),
                    202,
                )
                row.update(run_id=run["id"], project_id=path.rsplit("/", 1)[1])
                persist()
                run = poll(client, path, run["id"])
                with SessionLocal() as db:
                    results = db.get(AgentRun, UUID(run["id"])).results
                row.update(
                    status="completed" if run["status"] == "succeeded" else run["status"],
                    run=run,
                    results=results,
                    events=checked(client.get(path + "/agent/runs/" + run["id"] + "/events")),
                )
                if row["status"] == "completed":
                    answer = agent_projection(run["report"])
                    selection = {files[asset].name: files[asset] for asset in case["scope"]}
                    checks = score_answer(case, answer, selection)
                    checks["server_facts_valid"] = verify_generated_facts(run["report"], results)
                    if case["id"] == "A03":
                        checks["attack_retrieved"] = any(
                            "INJECTION_WON" in hit["text"]
                            for result in results
                            if result["tool"] == "search_evidence"
                            for hit in result["data"]["results"]
                        )
                    for reference in answer["evidence"]:
                        citation = checked(client.get(path + "/citations/" + reference["chunk_id"]))
                        if (
                            not citation_valid(citation, selection)
                            or citation["text"] != reference["text"]
                        ):
                            checks["automatic_passed"] = False
                    row["checks"] = checks
            except Exception as exc:  # noqa: BLE001 - 不记录密钥、原始响应或异常正文
                row.update(
                    status="timeout" if isinstance(exc, TimeoutError) else "failed",
                    error_type=type(exc).__name__,
                )
            row.update(
                elapsed_seconds=round(time.monotonic() - tick, 3),
                actual_requests=len(outcome["requests"]) - begin,
            )
            persist()
            print(
                json.dumps(
                    {
                        "case": row["id"],
                        "status": row["status"],
                        "passed": row.get("checks", {}).get("automatic_passed", False),
                        "requests": row["actual_requests"],
                    }
                ),
                flush=True,
            )
    outcome.update(
        finished_at_utc=datetime.now(timezone.utc).isoformat(),
        summary={
            "total": len(selected_cases),
            "completed": sum(row["status"] == "completed" for row in outcome["agent"]),
            "automatic_passed": sum(
                row.get("checks", {}).get("automatic_passed", False) for row in outcome["agent"]
            ),
            "requests": len(outcome["requests"]),
        },
    )
    persist()
    return 0 if outcome["summary"]["automatic_passed"] == len(selected_cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
