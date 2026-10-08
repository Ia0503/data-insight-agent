import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import anyio
import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.models.workspace import DataSource
from app.services.sources import source_path


@pytest.fixture(autouse=True)
def application_log_capture(caplog):
    logger = logging.getLogger("app")
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


def upload(client, project, name="sample.csv", content=b"a,b\n1,2\n"):
    return client.post(f"/api/projects/{project}/sources", files={"file": (name, content)})


def test_processing_events_and_safe_unexpected_error(client, project, monkeypatch, caplog):
    from app.services import sources

    response = upload(client, project)
    source = response.json()
    assert "event=ingestion_started" in caplog.text
    assert "event=ingestion_finished" in caplog.text
    assert f"source_id={source['id']}" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "a,b" not in caplog.text

    def fail(*args):
        raise RuntimeError("PRIVATE_CONTENT_DO_NOT_LOG")

    monkeypatch.setattr(sources, "inspect_file", fail)
    response = upload(client, project, content=b"secret,value\nprivate,1\n")
    assert response.json()["status"] == "failed"
    assert "exception_type=RuntimeError" in caplog.text
    assert "PRIVATE_CONTENT_DO_NOT_LOG" not in caplog.text
    assert "private,1" not in caplog.text


def test_unexpected_request_error_is_correlated_without_leaking(client, caplog):
    from app.main import app

    def fail():
        raise RuntimeError("DATABASE_PASSWORD_DO_NOT_LOG")

    app.dependency_overrides[get_db] = lambda: SimpleNamespace(add=lambda value: None, commit=fail)
    try:
        response = client.post("/api/projects", json={"name": "Private project(test)"})
    finally:
        app.dependency_overrides.pop(get_db)
    assert response.status_code == 500
    assert response.json()["detail"] == "服务处理失败，请重试或检查服务日志。"
    assert response.headers["x-request-id"] in caplog.text
    assert "DATABASE_PASSWORD_DO_NOT_LOG" not in caplog.text
    assert "Private project(test)" not in caplog.text


def test_missing_file_and_pdf_extraction_error(client, project, monkeypatch):
    source = upload(client, project).json()
    with SessionLocal() as db:
        item = db.get(DataSource, source["id"])
        source_path(item).unlink()
    base = f"/api/projects/{project}/sources/{source['id']}"
    assert client.get(base + "/download").status_code == 404
    assert client.get(base + "/preview").status_code == 409
    from pathlib import Path

    pdf = Path(__file__).resolve().parents[1] / "data" / "demo" / "quarterly_report.pdf"
    source = upload(client, project, "report.pdf", pdf.read_bytes()).json()
    from pypdf._page import PageObject

    def fail(self):
        raise RuntimeError("unreadable page")

    monkeypatch.setattr(PageObject, "extract_text", fail)
    base = f"/api/projects/{project}/sources/{source['id']}"
    assert client.get(base + "/preview").status_code == 409


def test_pdf_preview_reports_truncation(client, project, monkeypatch):
    from pathlib import Path

    from pypdf._page import PageObject

    pdf = Path(__file__).resolve().parents[1] / "data" / "demo" / "quarterly_report.pdf"
    source = upload(client, project, "report.pdf", pdf.read_bytes()).json()
    monkeypatch.setattr(PageObject, "extract_text", lambda self: "x" * 100001)
    preview = client.get(f"/api/projects/{project}/sources/{source['id']}/preview").json()
    assert preview["text_truncated"] is True
    assert len(preview["text"]) == 100000


def test_concurrent_retry_claims_source_once(client, project, monkeypatch):
    from app.services import sources

    source = upload(client, project, content=b"a,a\n1,2\n").json()
    with SessionLocal() as db:
        source_path(db.get(DataSource, source["id"])).write_bytes(b"a,b\n1,2\n")
    entered, release = Event(), Event()
    inspect = sources.inspect_file

    def wait_for_release(*args):
        entered.set()
        assert release.wait(5)
        return inspect(*args)

    monkeypatch.setattr(sources, "inspect_file", wait_for_release)
    path = f"/api/projects/{project}/sources/{source['id']}/retry"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, path)
        try:
            assert entered.wait(5)
            assert pool.submit(client.post, path).result(timeout=5).status_code == 409
        finally:
            release.set()
        assert first.result(timeout=5).json()["status"] == "ready"


def test_streamed_upload_limit_closes_parser_temp_files(client, project, monkeypatch):
    import starlette.formparsers as parsers
    from app.main import app

    temporary_files = []
    factory = parsers.SpooledTemporaryFile

    def tracked_file(*args, **kwargs):
        file = factory(*args, **kwargs)
        temporary_files.append(file)
        return file

    monkeypatch.setattr(parsers, "SpooledTemporaryFile", tracked_file)
    settings = get_settings()
    old_limit = settings.max_upload_mb
    settings.max_upload_mb = 1
    payload = (
        b'--audit\r\nContent-Disposition: form-data; name="file"; filename="large.csv"\r\nContent-Type: text/csv\r\n\r\n'
        + b"a\n" * (2 * 1024 * 1024)
        + b"\r\n--audit--\r\n"
    )
    chunks = iter(payload[index : index + 65536] for index in range(0, len(payload), 65536))
    messages = []
    consumed = 0

    async def receive():
        nonlocal consumed
        chunk = next(chunks, b"")
        consumed += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunk)}

    async def send(message):
        messages.append(message)

    async def request():
        await app(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/projects/{project}/sources",
                "query_string": b"",
                "root_path": "",
                "scheme": "http",
                "http_version": "1.1",
                "server": ("test", 80),
                "client": ("test", 1),
                "headers": [(b"content-type", b"multipart/form-data; boundary=audit")],
            },
            receive,
            send,
        )

    try:
        anyio.run(request)
    finally:
        settings.max_upload_mb = old_limit
    assert (
        next(message for message in messages if message["type"] == "http.response.start")["status"]
        == 413
    )
    assert consumed < len(payload)
    assert temporary_files and all(file.closed for file in temporary_files)


def test_recovery_events_and_header_validation(client, project, caplog):
    from app.main import app
    from fastapi.testclient import TestClient

    source = upload(client, project).json()
    with SessionLocal() as db:
        db.get(DataSource, source["id"]).status = "processing"
        db.commit()
    client.__exit__(None, None, None)
    with TestClient(app) as restarted:
        assert "event=ingestion_recovered count=1" in caplog.text
        assert restarted.get(f"/api/projects/{uuid4()}").status_code == 404
        assert upload(restarted, project, "bad\x7fname.csv").status_code == 415
