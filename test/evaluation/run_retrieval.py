"""Evaluate actual BGE embeddings against handwritten file/page/record targets."""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
if not url or make_url(url).database != "newai_test":
    raise RuntimeError("Evaluation requires newai_test.")
os.environ["DATABASE_URL"] = url
os.environ["UPLOAD_DIR"] = str(ROOT / "test/results/uploads")


def main():
    from alembic import command
    from alembic.config import Config
    from app.main import app
    from app.services.embeddings import provider
    from fastapi.testclient import TestClient

    command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    data_dir = ROOT / "test/data/business"
    expected = json.loads((data_dir / "expected.json").read_text("utf-8"))
    started = time.monotonic()
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "真实 BGE 检索评测(test)"}).json()[
            "id"
        ]
        path = f"/api/projects/{project}"
        for name in ["quarterly_report.pdf", "product_manual.pdf", "feedback.csv"]:
            uploaded = client.post(
                path + "/sources", files={"file": (name, (data_dir / name).read_bytes())}
            )
            assert uploaded.status_code == 201, uploaded.text
            assert uploaded.json()["status"] == "ready", uploaded.text
            options = (
                {"text_field": "text", "record_id_field": "feedback_id"}
                if name.endswith("csv")
                else {}
            )
            indexed = client.post(f"{path}/sources/{uploaded.json()['id']}/indexes", json=options)
            assert indexed.status_code == 202, indexed.text
            for _ in range(600):
                version = next(
                    i
                    for i in client.get(path + "/indexes").json()
                    if i["id"] == indexed.json()["id"]
                )
                if version["status"] in {"ready", "failed"}:
                    break
                time.sleep(0.2)
            assert version["status"] == "ready", version
        outcomes = []
        for case in expected["retrieval"]:
            tick = time.monotonic()
            response = client.post(
                path + "/search", json={"query": case["query"], "top_k": 5, "min_similarity": -1}
            )
            assert response.status_code == 200, response.text
            hits = response.json()["results"]
            ranks = [
                rank
                for rank, hit in enumerate(hits, 1)
                if hit["filename"] == case["filename"]
                and hit.get("page") == case.get("page")
                and hit.get("record") == case.get("record")
            ]
            for hit in hits:
                citation = client.get(path + "/citations/" + hit["chunk_id"])
                assert citation.status_code == 200
                assert citation.json()["text"] == hit["text"]
                assert citation.json()["start"] == hit["start"]
                if hit["page"]:
                    from app.ingestion.parsers import extract_pdf_text, read_pdf

                    original = extract_pdf_text(
                        read_pdf(data_dir / hit["filename"]).pages[hit["page"] - 1]
                    )
                else:
                    import pandas as pd

                    original = pd.read_csv(data_dir / hit["filename"], dtype=str).iloc[
                        hit["record"] - 1
                    ]["text"]
                assert original[hit["start"] : hit["end"]] == hit["text"]
            outcomes.append(
                {
                    "query": case["query"],
                    "expected": case,
                    "rank": min(ranks) if ranks else None,
                    "latency_ms": round((time.monotonic() - tick) * 1000, 1),
                    "hits": hits,
                }
            )
        negative = client.post(
            path + "/search",
            json={"query": "火星轨道的开普勒周期与黑洞的霍金辐射是什么？", "min_similarity": 0.55},
        ).json()
        report = {
            "model": provider.profile(),
            "dataset": "fictional-business-v1; 2 PDFs and 6 feedback records",
            "project_id": project,
            "cases": outcomes,
            "hit_at_5": sum(c["rank"] is not None for c in outcomes) / len(outcomes),
            "top1_accuracy": sum(c["rank"] == 1 for c in outcomes) / len(outcomes),
            "mrr_at_5": sum(1 / c["rank"] if c["rank"] else 0 for c in outcomes) / len(outcomes),
            "citation_checks": "Every returned slice matched the original file page/record and stored citation",
            "negative_query_results_at_055": len(negative["results"]),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "limits": "Five handwritten questions; not a general retrieval quality claim or a tuned threshold guarantee.",
        }
        (ROOT / "test/results").mkdir(parents=True, exist_ok=True)
        (ROOT / "test/results/retrieval-evaluation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"cases", "model"}}, ensure_ascii=False
            )
        )
        assert report["hit_at_5"] == 1, "Some golden evidence was not found; inspect actual report."
        assert not negative["results"], "Unrelated query passed threshold; inspect actual scores."


if __name__ == "__main__":
    main()
