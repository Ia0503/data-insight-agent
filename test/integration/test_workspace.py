from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings

DATA = Path(__file__).resolve().parents[1] / "data" / "demo"


def upload(client, project, filename, payload=None):
    data = payload if payload is not None else (DATA / filename).read_bytes()
    return client.post(f"/api/projects/{project}/sources", files={"file": (filename, data)})


def test_project_crud_and_validation(client, project):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/projects", json={"name": "  "}).status_code == 422
    assert client.get(f"/api/projects/{uuid4()}").status_code == 404
    assert (
        client.put(
            f"/api/projects/{project}", json={"name": "更新项目(test)", "description": "说明"}
        ).status_code
        == 200
    )
    assert client.get(f"/api/projects/{project}").json()["name"] == "更新项目(test)"
    assert any(item["id"] == project for item in client.get("/api/projects").json())


def test_csv_preview_download_and_project_isolation(client, project):
    source = upload(client, project, "sales.csv").json()
    assert source["status"] == "ready"
    assert "storage_key" not in source
    base = f"/api/projects/{project}/sources/{source['id']}"
    assert client.get(base + "/preview?page=2&page_size=2").json()["rows"][0]["revenue"] == ""
    assert client.get(base + "/preview?page_size=101").status_code == 422
    assert client.get(base + "/preview?page=0").status_code == 422
    assert client.get(base + "/download").content == (DATA / "sales.csv").read_bytes()
    other = client.post("/api/projects", json={"name": "Other(test)"}).json()["id"]
    assert client.get(f"/api/projects/{other}/sources").json() == []
    other_base = f"/api/projects/{other}/sources/{source['id']}"
    for suffix in ["/preview", "/download"]:
        assert client.get(other_base + suffix).status_code == 404
    assert client.post(other_base + "/retry").status_code == 404


def test_pdf_preview_and_wrong_content(client, project):
    source = upload(client, project, "quarterly_report.pdf").json()
    base = f"/api/projects/{project}/sources/{source['id']}"
    assert source["status"] == "ready"
    assert "Version 3.2" in client.get(base + "/preview?page=2").json()["text"]
    assert client.get(base + "/preview?page=3").status_code == 404
    fake = upload(client, project, "fake.pdf", b"a,b\n1,2\n").json()
    assert fake["status"] == "failed"


def test_failed_source_retry_and_filename_safety(client, project):
    response = upload(client, project, "../中文.csv", b"a,a\n1,2\n")
    assert response.status_code == 201
    source = response.json()
    assert source["filename"] == "中文.csv" and source["status"] == "failed"
    base = f"/api/projects/{project}/sources/{source['id']}"
    assert client.get(base + "/preview").status_code == 409
    assert client.post(base + "/retry").json()["status"] == "failed"
    from app.core.database import SessionLocal
    from app.models.workspace import DataSource
    from app.services.sources import source_path

    with SessionLocal() as db:
        item = db.get(DataSource, source["id"])
        path = source_path(item)
        assert path.parent == get_settings().upload_dir
        path.write_bytes(b"a,b\n1,2\n")
    assert client.post(base + "/retry").json()["status"] == "ready"
    assert client.post(base + "/retry").status_code == 409


def test_upload_limits_and_cleanup(client, project):
    assert upload(client, project, "empty.csv").status_code == 422
    assert upload(client, project, "script.exe", b"not csv").status_code == 415
    settings = get_settings()
    original = settings.max_upload_mb
    settings.max_upload_mb = 1
    before = set(settings.upload_dir.iterdir())
    try:
        # Fits middleware overhead allowance but exceeds per-file limit, so file cleanup is exercised.
        assert upload(client, project, "large.csv", b"a\n" + b"1\n" * 550000).status_code == 413
        assert set(settings.upload_dir.iterdir()) == before
        assert (
            client.post(
                f"/api/projects/{project}/sources",
                content=b"",
                headers={"content-length": str(3 * 1024 * 1024)},
            ).status_code
            == 413
        )
    finally:
        settings.max_upload_mb = original


def test_interrupted_ingestion_is_recoverable(client, project):
    from app.core.database import SessionLocal
    from app.models.workspace import DataSource

    source = upload(client, project, "sales.csv").json()
    with SessionLocal() as db:
        item = db.get(DataSource, source["id"])
        item.status = "processing"
        db.commit()
    from app.main import app
    from fastapi.testclient import TestClient

    # 真正释放原实例后重启；并行实例应被部署保护拒绝。
    client.__exit__(None, None, None)
    with TestClient(app) as restarted:
        files = restarted.get(f"/api/projects/{project}/sources").json()
        assert next(item for item in files if item["id"] == source["id"])["status"] == "failed"
        assert (
            restarted.post(f"/api/projects/{project}/sources/{source['id']}/retry").json()["status"]
            == "ready"
        )
