"""Prepare one local acceptance project via public APIs; never submit an LLM run."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data/comprehensive"
OUTPUT = ROOT / "results/comprehensive"
STATE = OUTPUT / "project.json"
INDEX_OPTIONS = {
    "客户反馈.csv": {"text_field": "反馈内容", "record_id_field": "反馈编号"},
    "季度经营简报.pdf": {},
    "产品与指标口径.pdf": {},
    "客服与版本复盘.pdf": {},
    "指令干扰反馈.csv": {"text_field": "反馈内容", "record_id_field": "反馈编号"},
}


def get_json(client, path, **kwargs):
    response = client.get(path, **kwargs)
    response.raise_for_status()
    return response.json()


def post_json(client, path, data):
    response = client.post(path, json=data)
    response.raise_for_status()
    return response.json()


def validate_assets():
    expected = json.loads((DATA / "expected.json").read_text(encoding="utf-8"))
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["case_id"] != expected["case_id"]
        or manifest["expected_sha256"]
        != hashlib.sha256((DATA / "expected.json").read_bytes()).hexdigest()
    ):
        raise RuntimeError(
            "Expected answers differ from the generated manifest; review and regenerate explicitly."
        )
    for name, asset in manifest["assets"].items():
        content = (DATA / name).read_bytes()
        if len(content) != asset["bytes"] or hashlib.sha256(content).hexdigest() != asset["sha256"]:
            raise RuntimeError(f"Fixture differs from its manifest: {name}")
    return expected, manifest


def choose_project(client, expected, verify_only):
    description = expected["description"] + "\n测试资料编号：" + expected["case_id"]
    projects = get_json(client, "/projects")
    if STATE.is_file():
        project_id = json.loads(STATE.read_text(encoding="utf-8"))["project_id"]
        matches = [project for project in projects if project["id"] == project_id]
    else:
        matches = [project for project in projects if project["name"] == expected["project_name"]]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous project name; no project was changed.")
    if matches:
        project = matches[0]
        if project["description"] != description:
            raise RuntimeError("Existing project has different ownership metadata; left unchanged.")
        return project["id"]
    if verify_only or STATE.is_file():
        raise RuntimeError("Prepared project no longer exists; will not recreate it implicitly.")
    project = post_json(
        client,
        "/projects",
        {"name": expected["project_name"], "description": description},
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"case_id": expected["case_id"], "project_id": project["id"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return project["id"]


def prepare(client, project_id, expected, manifest, verify_only):
    base = f"/projects/{project_id}"
    sources = get_json(client, base + "/sources")
    by_name = {}
    # 在任何补齐操作前核对全部已有来源；不能把同名但内容不同的文件覆盖掉。
    for source in sources:
        name = source["filename"]
        if name not in manifest["assets"] or name in by_name:
            raise RuntimeError("Unexpected or duplicate source; existing project left unchanged.")
        response = client.get(base + f"/sources/{source['id']}/download")
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != manifest["assets"][name]["sha256"]:
            raise RuntimeError(f"Existing source differs: {name}")
        if source["status"] != "ready":
            raise RuntimeError(f"Existing source is not ready: {name}")
        by_name[name] = source
    for name in manifest["assets"]:
        if name in by_name:
            continue
        if verify_only:
            raise RuntimeError(f"Missing source: {name}")
        response = client.post(
            base + "/sources", files={"file": (name, (DATA / name).read_bytes())}
        )
        response.raise_for_status()
        source = response.json()
        if source["status"] != "ready":
            raise RuntimeError(f"Ingestion failed: {name}; inspect workspace before retrying.")
        by_name[name] = source
        print(f"Uploaded: {name}", flush=True)
    order_id = by_name["订单明细.csv"]["id"]
    mapping_path = base + f"/sources/{order_id}/mapping"
    mapping = get_json(client, mapping_path)
    if mapping["fields"] and mapping["fields"] != expected["mapping"]:
        raise RuntimeError("Existing mapping differs; it was not overwritten.")
    if not mapping["fields"]:
        if verify_only:
            raise RuntimeError("Missing order mapping.")
        response = client.put(mapping_path, json={"fields": expected["mapping"]})
        response.raise_for_status()
        if not response.json()["valid"]:
            raise RuntimeError("Generated orders failed business validation.")
    for name, options in INDEX_OPTIONS.items():
        source_id = by_name[name]["id"]
        indexes = get_json(client, base + "/indexes")
        active = [
            index
            for index in indexes
            if index["source_id"] == source_id and index["active"] and index["status"] == "ready"
        ]
        if active:
            if (
                any(
                    active[0]["options"].get(key) != options.get(key)
                    for key in ["text_field", "record_id_field"]
                )
                or active[0]["content_hash"] != manifest["assets"][name]["sha256"]
            ):
                raise RuntimeError(f"Active index differs: {name}; left unchanged.")
            continue
        if verify_only:
            raise RuntimeError(f"Missing ready index: {name}")
        running = [
            index
            for index in indexes
            if index["source_id"] == source_id and index["status"] in {"queued", "processing"}
        ]
        index = (
            running[0]
            if running
            else post_json(client, base + f"/sources/{source_id}/indexes", options)
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            index = next(
                item for item in get_json(client, base + "/indexes") if item["id"] == index["id"]
            )
            if index["status"] == "ready":
                print(f"Index ready: {name} ({index['chunk_count']} chunks)", flush=True)
                break
            if index["status"] == "failed":
                raise RuntimeError(f"Index failed: {name}; inspect index error in UI.")
            time.sleep(1)
        else:
            raise TimeoutError(f"Index still running: {name}; it was not cancelled.")
    return {name: source["id"] for name, source in by_name.items()}


def verify(client, project_id, ids, expected):
    base = f"/projects/{project_id}"
    metrics_path = base + f"/sources/{ids['订单明细.csv']}/metrics"
    results = []
    for month, gold in expected["monthly"].items():
        end_day = "31" if month in {"2026-07", "2026-08"} else "30"
        for metric in ["paid_sales", "net_sales", "paid_orders", "refund_rate"]:
            result = post_json(
                client,
                metrics_path,
                {
                    "metric": metric,
                    "start": month + "-01",
                    "end": month + "-" + end_day,
                    "compare_previous": month == "2026-09",
                },
            )
            if (
                result["value"] != gold[metric]
                or result["statistics"]["refund_order_count"] != gold["refund_orders"]
            ):
                raise RuntimeError(f"Metric differs from handwritten expectation: {month}/{metric}")
            if (
                result["evidence"]["record_count"] != 600
                or not result["evidence"]["records_truncated"]
                or len(result["evidence"]["records"]) != 100
            ):
                raise RuntimeError(
                    "Metric must use all 600 rows while limiting evidence display to 100."
                )
            if (
                month == "2026-09"
                and result["comparison"]["change_percent"]
                != expected["september_change_percent"][metric]
            ):
                raise RuntimeError(f"Comparison differs: {metric}")
            results.append(result)
    for group in ["region", "product"]:
        result = post_json(
            client,
            metrics_path,
            {
                "metric": "net_sales",
                "start": "2026-09-01",
                "end": "2026-09-30",
                "group": group,
            },
        )
        if {item["group"]: item["value"] for item in result["groups"]} != expected[
            f"september_net_by_{group}"
        ]:
            raise RuntimeError(f"Grouping differs: {group}")
        results.append(result)
    for metric, value in expected["total"].items():
        result = post_json(
            client,
            metrics_path,
            {"metric": metric, "start": "2026-06-01", "end": "2026-09-30"},
        )
        if result["value"] != value:
            raise RuntimeError(f"Total differs: {metric}")
        results.append(result)
    empty = post_json(
        client,
        metrics_path,
        {"metric": "refund_rate", "start": "2026-10-01", "end": "2026-10-31"},
    )
    if empty["value"] is not None or empty["statistics"]["paid_order_count"] != 0:
        raise RuntimeError("Empty-period refund rate must be undefined.")
    unmapped = client.post(
        base + f"/sources/{ids['未映射订单样本.csv']}/metrics",
        json={"metric": "net_sales", "start": "2026-09-01", "end": "2026-09-30"},
    )
    if unmapped.status_code != 409:
        raise RuntimeError("Unmapped sample must refuse metric calculation.")
    tools_path = base + f"/sources/{ids['订单明细.csv']}/tools"
    schema = post_json(client, tools_path, {"tool": "get_schema"})
    selected = post_json(
        client,
        tools_path,
        {
            "tool": "filter_data",
            "filters": [{"field": "订单编号", "value": "001831"}],
            "limit": 1,
        },
    )
    if (
        schema["row_count"] != 2440
        or selected["total"] != 1
        or selected["rows"][0]["record"] != 1831
    ):
        raise RuntimeError("Schema or leading-zero filter differs.")
    grouped = post_json(
        client,
        tools_path,
        {"tool": "group_by", "group": "订单状态", "aggregation": "count"},
    )
    if {item["group"]: int(item["value"]) for item in grouped["groups"]} != {
        "paid": 2217,
        "refunded": 183,
        "cancelled": 40,
    }:
        raise RuntimeError("Controlled status grouping differs.")
    feedback = get_json(
        client,
        base + f"/sources/{ids['客户反馈.csv']}/preview",
        params={"page": 5, "page_size": 20},
    )
    if (
        feedback["total_rows"] != 120
        or feedback["rows"][10]["反馈编号"] != "F0091"
        or "\n" not in feedback["rows"][10]["反馈内容"]
    ):
        raise RuntimeError("Multi-line feedback pagination differs.")
    evidence = []
    for check in expected["retrieval_checks"]:
        source_id = ids[check["source"]]
        result = post_json(
            client,
            base + "/search",
            {
                "query": check["query"],
                "source_ids": [source_id],
                "top_k": 5,
                "min_similarity": 0.35,
            },
        )
        if not any(check["contains"] in hit["text"] for hit in result["results"]):
            raise RuntimeError(f"Expected retrieval evidence not found: {check['source']}")
        for hit in result["results"]:
            citation = get_json(client, base + f"/citations/{hit['chunk_id']}")
            if citation["source_id"] != source_id or citation["text"] != hit["text"]:
                raise RuntimeError("Citation does not match selected source or retrieved text.")
            params = {
                "page": citation["page"] or (citation["record"] - 1) // 20 + 1,
                "page_size": 20,
                "expected_hash": citation["content_hash"],
            }
            preview = get_json(client, base + f"/sources/{source_id}/preview", params=params)
            original = (
                preview["text"]
                if citation["page"]
                else preview["rows"][(citation["record"] - 1) % 20]["反馈内容"]
            )
            if original[citation["start"] : citation["end"]] != citation["text"]:
                raise RuntimeError("Citation character range differs from original preview.")
            evidence.append(citation)
    return {
        "metric_checks": len(results) + 1,
        "metrics": results,
        "empty_period": empty,
        "unmapped_status": 409,
        "tool_checks": 3,
        "retrieval_checks": len(expected["retrieval_checks"]),
        "citations": evidence,
        "multiline_preview": True,
        "llm_requests": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只读核对现有综合验收项目，不创建或补齐",
    )
    args = parser.parse_args()
    expected, manifest = validate_assets()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api", timeout=120, follow_redirects=False
    ) as client:
        project_id = choose_project(client, expected, args.verify_only)
        ids = prepare(client, project_id, expected, manifest, args.verify_only)
        state = {
            "case_id": expected["case_id"],
            "project_id": project_id,
            "url": f"http://localhost:5173/projects/{project_id}",
            "sources": ids,
            "main_question": expected["main_question"],
            "main_source_ids": [ids[name] for name in expected["main_sources"]],
        }
        # 只读验证不改后端数据；诊断产物始终留在忽略的 test/results 内。
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = verify(client, project_id, ids, expected)
        (OUTPUT / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"Ready: {state['url']}; 23 metric, 3 tool, 3 retrieval checks; no LLM requests.",
            flush=True,
        )


if __name__ == "__main__":
    main()
