"""Explicitly add a fictional example to the running local development app."""

from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent / "data/business"
NAME = "业务分析示例(test)"


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000/api", timeout=120) as client:
        projects = client.get("/projects")
        projects.raise_for_status()
        existing = next(
            (p for p in projects.json() if p["name"] in {NAME, "业务分析示例（虚构数据）"}), None
        )
        if existing:
            print(f"Example exists; left unchanged. /projects/{existing['id']}/analysis")
            return
        response = client.post(
            "/projects",
            json={
                "name": NAME,
                "description": "人工构造的订单、销售汇总、用户反馈和中文 PDF。汇总与明细不能重复计算；仅用于功能检查。",
            },
        )
        response.raise_for_status()
        project = response.json()["id"]
        for name in [
            "sales_summary.csv",
            "feedback.csv",
            "product_manual.pdf",
            "quarterly_report.pdf",
            "orders.csv",
        ]:
            uploaded = client.post(
                f"/projects/{project}/sources", files={"file": (name, (DATA / name).read_bytes())}
            )
            uploaded.raise_for_status()
            source = uploaded.json()
            if source["status"] != "ready":
                raise RuntimeError("Example file failed ingestion; inspect workspace.")
            if name == "orders.csv":
                fields = {
                    f: f
                    for f in [
                        "order_id",
                        "paid_at",
                        "paid_amount",
                        "refund_amount",
                        "region",
                        "product",
                        "status",
                    ]
                }
                mapped = client.put(
                    f"/projects/{project}/sources/{source['id']}/mapping", json={"fields": fields}
                )
                mapped.raise_for_status()
                assert mapped.json()["valid"]
            elif name in {"feedback.csv", "product_manual.pdf", "quarterly_report.pdf"}:
                options = (
                    {"text_field": "text", "record_id_field": "feedback_id"}
                    if name.endswith("csv")
                    else {}
                )
                result = client.post(
                    f"/projects/{project}/sources/{source['id']}/indexes", json=options
                )
                result.raise_for_status()
        print(
            f"Added fictional example. /projects/{project}/analysis; indexes update in background."
        )


if __name__ == "__main__":
    main()
