"""Import small ingestion examples into the local development workspace."""

from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent / "data" / "demo"
with httpx.Client(base_url="http://127.0.0.1:8000/api", timeout=60) as client:
    response = client.get("/projects")
    response.raise_for_status()
    project = next(
        (
            item
            for item in response.json()
            if item["name"] in {"SaaS 数据导入示例(test)", "SaaS 数据导入示例"}
        ),
        None,
    )
    if project is None:
        response = client.post(
            "/projects",
            json={
                "name": "SaaS 数据导入示例(test)",
                "description": "少量虚构文件，用于检查上传、字段质量与 PDF 文本预览。尚不提供 AI 分析。",
            },
        )
        response.raise_for_status()
        project = response.json()
    project_id = project["id"]
    response = client.get(f"/projects/{project_id}/sources")
    response.raise_for_status()
    existing = {item["filename"] for item in response.json()}
    for name in ["sales.csv", "quarterly_report.pdf"]:
        if name in existing:
            continue
        with (DATA / name).open("rb") as file:
            response = client.post(f"/projects/{project_id}/sources", files={"file": (name, file)})
        response.raise_for_status()
        assert response.json()["status"] == "ready", response.json()["error"]
print(f"Local demo ready: http://127.0.0.1:5173/projects/{project_id}")
