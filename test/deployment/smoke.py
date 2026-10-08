"""Exercise only an explicitly isolated localhost Compose test environment."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.hostname not in {"127.0.0.1", "localhost"} or url.port not in {8081, 8082}:
        raise RuntimeError("This test may only write to the isolated 8081/8082 environments.")
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "test/results").resolve()) or output.exists():
        raise RuntimeError("Use a new output file under test/results.")
    output.parent.mkdir(parents=True, exist_ok=True)
    checks = []
    with httpx.Client(base_url=args.url, timeout=130, trust_env=False) as client:

        def checked(response, status=200):
            if response.status_code != status:
                raise RuntimeError(
                    f"Unexpected HTTP status: {response.status_code}; response body omitted."
                )
            return response.json()

        assert checked(client.get("/api/health"))["database"] == "connected"
        html = client.get("/")
        assert html.status_code == 200 and "Content-Security-Policy" in html.headers
        checks.extend(["database-health", "frontend-security-headers"])
        project = checked(
            client.post(
                "/api/projects",
                json={
                    "name": "容器部署验收(test)",
                    "description": "虚构资料，仅用于隔离部署验证。",
                },
            ),
            201,
        )
        path = f"/api/projects/{project['id']}"
        data = ROOT / "test/data/business"
        sources = {}
        for filename in ("orders.csv", "feedback.csv", "quarterly_report.pdf"):
            item = checked(
                client.post(
                    path + "/sources", files={"file": (filename, (data / filename).read_bytes())}
                ),
                201,
            )
            assert item["status"] == "ready"
            sources[filename] = item["id"]
            download = client.get(path + f"/sources/{item['id']}/download")
            assert download.status_code == 200
            assert (
                hashlib.sha256(download.content).digest()
                == hashlib.sha256((data / filename).read_bytes()).digest()
            )
        checks.extend(["csv-pdf-upload", "file-download-hashes"])
        fields = (
            "order_id",
            "paid_at",
            "paid_amount",
            "refund_amount",
            "status",
            "region",
            "product",
        )
        saved = checked(
            client.put(
                path + f"/sources/{sources['orders.csv']}/mapping",
                json={"fields": {field: field for field in fields}},
            )
        )
        assert saved["valid"]
        result = checked(
            client.post(
                path + f"/sources/{sources['orders.csv']}/metrics",
                json={
                    "metric": "net_sales",
                    "start": "2026-09-01",
                    "end": "2026-09-30",
                    "compare_previous": True,
                    "group": "region",
                },
            )
        )
        assert result["value"] == "76000.00"
        checks.append("mapped-metric-with-comparison")
        for filename in ("feedback.csv", "quarterly_report.pdf"):
            options = (
                {"text_field": "text", "record_id_field": "feedback_id"}
                if filename.endswith("csv")
                else {}
            )
            index = checked(
                client.post(path + f"/sources/{sources[filename]}/indexes", json=options), 202
            )
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                state = next(
                    item
                    for item in checked(client.get(path + "/indexes"))
                    if item["id"] == index["id"]
                )
                if state["status"] == "ready":
                    break
                if state["status"] == "failed":
                    raise RuntimeError("Isolated BGE indexing failed; raw file contents omitted.")
                time.sleep(0.5)
            else:
                raise RuntimeError("Indexing deadline reached.")
        results = checked(
            client.post(
                path + "/search",
                json={
                    "query": "新版本闪退和退款",
                    "source_ids": [sources["feedback.csv"], sources["quarterly_report.pdf"]],
                    "top_k": 5,
                    "min_similarity": 0.35,
                },
            )
        )
        assert results["results"]
        for item in results["results"]:
            citation = checked(client.get(path + "/citations/" + item["chunk_id"]))
            assert citation["text"] == item["text"]
        checks.extend(["local-bge-pdf-csv-indexes", "retrieval-citation-text"])
        config = checked(client.get(path + "/agent/configuration"))
        assert config["password_required"] and config["access_configured"] and not config["enabled"]
        assert config["quota"]["limit"] == 1_000_000
        refused = client.post(
            path + "/agent/runs",
            json={
                "question": "隔离测试，不应请求生成模型。",
                "source_ids": list(sources.values()),
                "request_id": str(uuid4()),
            },
        )
        assert refused.status_code == 503
        checks.append("generation-disabled-and-protection-configured")
        history = checked(client.get(path + "/agent/history"))
        assert history["items"] == []
        report = {
            "project_id": project["id"],
            "sources": sources,
            "checks": checks,
            "llm_requests": 0,
        }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"checks_passed": len(checks), "llm_requests": 0}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"Deployment smoke failed ({type(exc).__name__}); no automatic retry.", file=sys.stderr
        )
        raise
