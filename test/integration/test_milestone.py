"""Regression cases found by the step-one-to-three milestone audit."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from app.agent.tools import ToolExecutor, capture_snapshot
from app.core.database import SessionLocal
from app.models.workspace import DataSource
from app.services.analysis import FIELDS, file_hash
from app.services.sources import source_path

DATA = Path(__file__).resolve().parents[1] / "data" / "business"


def upload(client, project, name, content):
    response = client.post(f"/api/projects/{project}/sources", files={"file": (name, content)})
    assert response.status_code == 201 and response.json()["status"] == "ready"
    return response.json()["id"]


def test_raw_csv_columns_and_filter_values_survive_api_and_agent_validation(client, project):
    source = upload(client, project, "spaces.csv", b" region , amount \n East ,10\nEast,20\n")
    path = f"/api/projects/{project}/sources/{source}"
    payload = {"tool": "filter_data", "filters": [{"field": " region ", "value": " East "}]}
    response = client.post(path + "/tools", json=payload)
    assert response.status_code == 200, response.text
    assert [row["record"] for row in response.json()["rows"]] == [1]
    grouped = client.post(
        path + "/tools",
        json={"tool": "group_by", "group": " region ", "value": " amount ", "aggregation": "sum"},
    )
    assert grouped.status_code == 200, grouped.text
    assert {row["group"]: row["value"] for row in grouped.json()["groups"]} == {
        " East ": "10",
        "East": "20",
    }
    with SessionLocal() as db:
        snapshot = capture_snapshot(db, UUID(project), [UUID(source)])
    executor = ToolExecutor(UUID(project), snapshot)
    filtered = executor.execute(
        "filter_data", json.dumps({"source_id": source, "filters": payload["filters"]}), "R1"
    )
    assert filtered["data"]["rows"][0]["values"][" region "] == " East "
    grouped = executor.execute(
        "group_by", json.dumps({"source_id": source, "group": " region "}), "R2"
    )
    assert len(grouped["data"]["groups"]) == 2


def test_business_mapping_preserves_exact_csv_column_names(client, project):
    header, body = (DATA / "orders.csv").read_text("utf-8-sig").split("\n", 1)
    content = (",".join(f" {name} " for name in header.split(",")) + "\n" + body).encode()
    source = upload(client, project, "orders-spaces.csv", content)
    path = f"/api/projects/{project}/sources/{source}"
    response = client.put(
        path + "/mapping", json={"fields": {field: f" {field} " for field in FIELDS}}
    )
    assert response.status_code == 200 and response.json()["valid"], response.text
    metric = client.post(path + "/metrics", json={"start": "2026-09-01", "end": "2026-09-30"})
    assert metric.status_code == 200 and metric.json()["value"] == "76000.00"


@pytest.mark.parametrize("kind", ["csv", "pdf"])
@pytest.mark.parametrize("during_read", [False, True])
def test_historical_preview_and_download_reject_changed_file(
    client, project, monkeypatch, kind, during_read
):
    from app.api import workspace

    name = "orders.csv" if kind == "csv" else "quarterly_report.pdf"
    source = upload(client, project, name, (DATA / name).read_bytes())
    url = f"/api/projects/{project}/sources/{source}"
    with SessionLocal() as db:
        path = source_path(db.get(DataSource, UUID(source)))
    digest = file_hash(path)
    params = {"expected_hash": digest}
    assert client.get(url + "/preview", params=params).status_code == 200
    assert client.get(url + "/download", params=params).status_code == 200
    original = path.read_bytes()
    if during_read:
        name = "csv_preview" if kind == "csv" else "extract_pdf_text"
        read = getattr(workspace, name)

        def replace_after_read(*args, **kwargs):
            result = read(*args, **kwargs)
            path.write_bytes(original + b"\n")
            return result

        monkeypatch.setattr(workspace, name, replace_after_read)
    else:
        path.write_bytes(original + b"\n")
    response = client.get(url + "/preview", params=params)
    assert response.status_code == 409 and "原文件已变化" in response.json()["detail"]
    assert client.get(url + "/download", params=params).status_code == 409
    # Ordinary workspace preview remains available; only the stale historical locator is refused.
    assert client.get(url + "/preview").status_code == 200
    assert client.get(url + "/preview", params={"expected_hash": "invalid"}).status_code == 422
