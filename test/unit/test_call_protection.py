import asyncio
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace

import pytest
from app.agent.graph import AgentWorkflow
from app.agent.model import ModelError
from app.core.access import parse_hash, password_hash, verify_password
from app.core.config import Settings
from app.core.database import acquire_instance, release_instance
from app.services.llm_budget import hour_start, reported_tokens, request_bound
from sqlalchemy.engine import make_url


def test_salted_hash_and_unicode_without_plaintext():
    password = "test-only-中文-password"
    first, second = password_hash(password), password_hash(password)
    assert first != second and password not in first
    assert verify_password(password, first)
    assert not verify_password(password + "x", first)
    assert not verify_password("", first)
    assert not verify_password("x" * 129, first)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "YOUR_PASSWORD_HASH_HERE",
        "pbkdf2_sha256$1$x$x",
        "pbkdf2_sha256$999999999999$bad$bad",
        "invalid$260000$bad$bad",
    ],
)
def test_bad_hash_fails_closed(value):
    assert parse_hash(value) is None
    assert not verify_password("test-only-password", value)


@pytest.mark.parametrize("value", ["", "x" * 129])
def test_hash_rejects_invalid_password_length(value):
    with pytest.raises(ValueError):
        password_hash(value)


def test_fixed_hour_boundary_and_conservative_chinese_bound():
    before = datetime(2026, 10, 7, 0, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 7, 1, 0, 0, tzinfo=timezone.utc)
    assert hour_start(before).hour == 0 and hour_start(after).hour == 1
    assert hour_start(before).minute == 0
    with pytest.raises(ValueError):
        hour_start(datetime(2026, 10, 7))
    assert request_bound({"messages": [{"content": "中文" * 100}]}) > 600 + 4096


@pytest.mark.parametrize(
    "usage, expected",
    [
        (None, None),
        ({}, None),
        ({"total_tokens": True}, None),
        ({"total_tokens": -1}, None),
        ({"total_tokens": 7}, 7),
        ({"total_tokens": 7, "prompt_tokens": 5}, None),
        ({"total_tokens": 7, "prompt_tokens": 5, "completion_tokens": 3}, None),
        ({"total_tokens": 7, "prompt_tokens": 5, "completion_tokens": 2}, 7),
    ],
)
def test_usage_requires_a_consistent_total(usage, expected):
    assert reported_tokens(usage) == expected


def test_container_override_preserves_encoded_credentials():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:test%40only%23password@127.0.0.1:55432/newai_test",
        database_host="db",
        database_port=5432,
    )
    url = make_url(settings.database_url.get_secret_value())
    assert url.host == "db" and url.port == 5432 and url.password == "test@only#password"
    assert "test@only#password" not in repr(settings)


def test_single_instance_guard_releases_after_close(migrate_test_database):
    first = acquire_instance()
    try:
        with pytest.raises(RuntimeError, match="另一个"):
            acquire_instance()
    finally:
        release_instance(first)
    third = acquire_instance()
    release_instance(third)


@pytest.mark.parametrize("slow_emit", [False, True])
def test_expired_deadline_cannot_dispatch_a_new_request(slow_emit):
    class NeverCalled:
        config = SimpleNamespace(timeout=1)
        calls = 0

        async def complete(self, *args):
            self.calls += 1
            pytest.fail("Expired tasks must not send model requests.")

    async def emit(*args, **kwargs):
        if slow_emit:
            await asyncio.sleep(0.03)

    model = NeverCalled()
    workflow = AgentWorkflow(
        SimpleNamespace(), model, None, emit, deadline=monotonic() + (0.01 if slow_emit else -1)
    )
    with pytest.raises(TimeoutError):
        asyncio.run(workflow.ask([]))
    assert model.calls == 0


@pytest.mark.parametrize(
    "model_timeout, callback_time, expected",
    [(0.5, 0.5, ModelError), (0.5, 2, TimeoutError), (60, 0.999, TimeoutError)],
)
def test_model_wait_distinguishes_total_deadline_without_timer_race(
    monkeypatch, model_timeout, callback_time, expected
):
    from app.agent import graph

    clock = {"now": 0}
    monkeypatch.setattr(graph, "monotonic", lambda: clock["now"])

    class TimedOut:
        config = SimpleNamespace(timeout=model_timeout)
        calls = 0

        async def complete(self, *args):
            self.calls += 1
            clock["now"] = callback_time
            raise TimeoutError

    async def emit(*args, **kwargs):
        pass

    model = TimedOut()
    workflow = AgentWorkflow(SimpleNamespace(), model, None, emit, deadline=1)
    with pytest.raises(expected):
        asyncio.run(workflow.ask([]))
    assert model.calls == 1
