import json
from uuid import uuid4

import pytest
from app.core.access import password_hash
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.agent import AgentRun
from app.models.call_protection import AccessAttempt
from sqlalchemy import delete, func, select
from test_agent import ScriptedModel, enable_fake, source, wait

PASSWORD = "test-only-call-password"


@pytest.fixture
def protected(client, project, monkeypatch):
    identifier = source(client, project)
    fake = ScriptedModel(identifier)
    enable_fake(monkeypatch, fake)
    monkeypatch.setattr(get_settings(), "analysis_password_required", True)
    from pydantic import SecretStr

    monkeypatch.setattr(
        get_settings(), "analysis_password_hash", SecretStr(password_hash(PASSWORD))
    )
    with SessionLocal() as db:
        db.execute(delete(AccessAttempt))
        db.commit()
    return identifier, fake


def submit(client, project, identifier, password=None, request_id=None):
    return client.post(
        f"/api/projects/{project}/agent/runs",
        json={
            "question": "比较九月与八月净销售额。",
            "source_ids": [identifier],
            "request_id": str(request_id or uuid4()),
        },
        headers={"X-Analysis-Password": password} if password else {},
    )


def test_api_requires_a_fresh_password_for_each_new_task(client, project, protected):
    identifier, fake = protected
    assert submit(client, project, identifier).status_code == 401
    assert submit(client, project, identifier, "test-only-wrong-password").status_code == 401
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(AgentRun).where(AgentRun.project_id == project)
            )
            == 0
        )
    assert fake.calls == 0
    created = submit(client, project, identifier, PASSWORD)
    assert created.status_code == 202
    completed = wait(client, project, created.json()["id"])
    assert completed["status"] == "succeeded"
    assert submit(client, project, identifier).status_code == 401
    assert PASSWORD not in json.dumps(completed)


def test_idempotent_confirmation_does_not_request_or_charge_again(client, project, protected):
    identifier, fake = protected
    request_id = uuid4()
    created = submit(client, project, identifier, PASSWORD, request_id)
    completed = wait(client, project, created.json()["id"])
    calls = fake.calls
    repeated = submit(client, project, identifier, request_id=request_id)
    assert repeated.status_code == 202 and repeated.json()["id"] == completed["id"]
    assert fake.calls == calls


def test_unconfigured_hash_fails_closed_and_never_echoes_secret(
    client, project, protected, monkeypatch
):
    identifier, fake = protected
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "analysis_password_hash", SecretStr(""))
    config = client.get(f"/api/projects/{project}/agent/configuration").json()
    assert config["password_required"] and not config["access_configured"] and not config["enabled"]
    response = submit(client, project, identifier, PASSWORD)
    assert response.status_code == 503 and PASSWORD not in response.text and fake.calls == 0


def test_failed_attempt_limit_is_persisted(client, project, protected):
    identifier, fake = protected
    for _ in range(6):
        assert submit(client, project, identifier, "test-only-wrong-password").status_code == 401
    denied = submit(client, project, identifier, PASSWORD)
    assert denied.status_code == 429 and int(denied.headers["Retry-After"]) > 0
    assert fake.calls == 0
    with SessionLocal() as db:
        attempts = db.scalars(select(AccessAttempt)).all()
        assert len(attempts) == 1 and attempts[0].failures == 6 and attempts[0].blocked_until
        assert PASSWORD not in json.dumps([attempt.key for attempt in attempts])


def test_quota_exhaustion_refuses_new_task_without_model_calls(
    client, project, protected, monkeypatch
):
    identifier, fake = protected
    monkeypatch.setattr(get_settings(), "llm_hourly_token_limit", 1)
    response = submit(client, project, identifier, PASSWORD)
    assert response.status_code == 429 and "额度不足" in response.text and fake.calls == 0
