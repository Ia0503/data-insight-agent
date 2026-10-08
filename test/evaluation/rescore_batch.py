"""只读已保存答案并离线重评分；不启动应用、访问数据库或调用生成模型。"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test"))
sys.path.insert(0, str(ROOT / "backend"))
from evaluation.batch_scoring import agent_projection, score_answer, summarize  # noqa: E402
from evaluation.run_batch import CASES, RESULTS, digest, inputs, save  # noqa: E402


def rescore(source, output):
    source, output = source.resolve(), output.resolve()
    if (
        not source.is_relative_to(RESULTS.resolve())
        or not output.is_relative_to(RESULTS.resolve())
        or output.exists()
    ):
        raise ValueError("Source/result must stay inside test/results; never overwrite.")
    data = json.loads(CASES.read_text("utf-8"))
    files, hashes = inputs(data)
    result = json.loads(source.read_text("utf-8"))
    if result["mode"] != "live" or result["manifest"]["files"] != hashes:
        raise ValueError("Original frozen fixtures do not match.")
    if (
        any(len(result[arm]) != len(data["agents"]) for arm in ("agent", "baseline"))
        or {r["id"] for r in result["agent"]} != {c["id"] for c in data["agents"]}
        or {r["id"] for r in result["baseline"]} != {c["id"] for c in data["agents"]}
    ):
        raise ValueError("Missing cases cannot be silently omitted.")
    cases = {case["id"]: case for case in data["agents"]}
    revisions = []
    for arm in ("baseline", "agent"):
        for row in result[arm]:
            if row["status"] != "completed":
                continue
            case = cases[row["id"]]
            selected = {files[asset].name: files[asset] for asset in case["scope"]}
            answer = row["answer"] if arm == "baseline" else agent_projection(row["run"]["report"])
            before = row["checks"]
            checks = score_answer(case, answer, selected)
            # 服务端事实、实际恶意片段暴露等检查沿用原独立执行记录，不重新证明。
            for name in ("server_facts_valid", "attack_retrieved"):
                if name in before:
                    checks[name] = before[name]
            revisions.append({"id": row["id"], "arm": arm, "before": before, "after": checks})
            row["checks"] = checks
    result["rescore"] = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": digest(source),
        "scorer_sha256": digest(ROOT / "test/evaluation/batch_scoring.py"),
        "reason": "CSV完整记录与解码单元格都是已提供给基线的原文；修正记录定位，不改变数值、用例或生成提示。",
        "extra_model_requests": 0,
        "revisions": revisions,
    }
    result["summary"] = {
        arm: {
            split: summarize([row for row in result[arm] if row["split"] == split])
            for split in ("development", "heldout")
        }
        for arm in ("agent", "baseline")
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    save(output, result)
    return result["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(rescore(args.source, args.output), ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001 - 原输入错误不得回显原文件内容
        print(json.dumps({"status": "incomplete", "error_type": type(exc).__name__}))
        sys.exit(1)
