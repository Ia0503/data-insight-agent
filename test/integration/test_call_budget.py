import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from app.agent.model import ChatModel, ModelConfig, ModelError
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.agent import AgentRun
from app.models.call_protection import TokenHour, TokenReservation
from app.services import llm_budget as budget
from sqlalchemy import delete, select


@pytest.fixture
def isolated_budget(client, project, monkeypatch):
    start = datetime(2090, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.execute(
            delete(TokenReservation).where(
                TokenReservation.hour_start.in_([start, start + timedelta(hours=1)])
            )
        )
        db.execute(
            delete(TokenHour).where(TokenHour.hour_start.in_([start, start + timedelta(hours=1)]))
        )
        run = AgentRun(
            project_id=project,
            request_id=uuid4(),
            question="限额验证(test)",
            provider="custom",
            model="test-only",
            status="failed",
            snapshot={"sources": []},
        )
        db.add(run)
        db.commit()
        identifier = run.id
    monkeypatch.setattr(get_settings(), "llm_hourly_token_limit", 100)
    yield identifier, start


def status(start):
    with SessionLocal() as db:
        return budget.quota_status(db, start)


def test_atomic_concurrent_reservations_cannot_overbook(isolated_budget):
    identifier, start = isolated_budget

    def reserve(_):
        try:
            return budget.reserve_tokens(identifier, 40, start)
        except budget.QuotaError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))
    assert len([value for value in results if value]) == 2
    assert status(start)["reserved"] == 80 and status(start)["remaining"] == 20


def test_usage_reconciliation_is_idempotent_and_unknown_is_charged(isolated_budget):
    identifier, start = isolated_budget
    first = budget.reserve_tokens(identifier, 40, start)
    usage = {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20}
    budget.settle_tokens(first, usage)
    budget.settle_tokens(first, usage)
    second = budget.reserve_tokens(identifier, 40, start)
    budget.settle_tokens(second, None)
    value = status(start)
    assert value["used"] == 60 and value["reserved"] == 0 and value["unknown_requests"] == 1


def test_dispatch_hour_is_preserved_when_reply_arrives_after_reset(isolated_budget):
    identifier, start = isolated_budget
    first = budget.reserve_tokens(identifier, 100, start + timedelta(minutes=59))
    next_hour = start + timedelta(hours=1)
    second = budget.reserve_tokens(identifier, 100, next_hour)
    budget.settle_tokens(first, {"total_tokens": 30})
    budget.settle_tokens(second, {"total_tokens": 25})
    assert status(start)["used"] == 30 and status(next_hour)["used"] == 25
    assert status(next_hour)["remaining"] == 75


def test_interrupted_reservations_are_conservatively_recovered_once(isolated_budget):
    identifier, start = isolated_budget
    budget.reserve_tokens(identifier, 100, start)
    with SessionLocal() as db:
        assert budget.settle_unfinished(db, identifier) == 1
        db.commit()
    with SessionLocal() as db:
        assert budget.settle_unfinished(db, identifier) == 0
        db.commit()
    assert status(start)["used"] == 100 and status(start)["remaining"] == 0
    with pytest.raises(budget.QuotaError):
        budget.reserve_tokens(identifier, 1, start)


def test_unexpected_provider_usage_blocks_current_hour_but_is_not_lost(isolated_budget):
    identifier, start = isolated_budget
    reservation = budget.reserve_tokens(identifier, 40, start)
    with pytest.raises(budget.QuotaError, match="超过预留"):
        budget.settle_tokens(reservation, {"total_tokens": 130})
    assert (
        status(start)["used"] == 130
        and status(start)["blocked"]
        and status(start)["remaining"] == 0
    )
    with pytest.raises(budget.QuotaError):
        budget.reserve_tokens(identifier, 1, start)
    assert status(start + timedelta(hours=1))["remaining"] == 100


@pytest.mark.parametrize(
    "mode",
    ["known", "missing", "invalid", "http_error", "cancel", "inconsistent", "malformed_usage"],
)
def test_real_model_adapter_settles_usage_on_all_outcomes(isolated_budget, monkeypatch, mode):
    identifier, start = isolated_budget
    monkeypatch.setattr(get_settings(), "llm_hourly_token_limit", 20_000)
    original_hour = budget.hour_start
    monkeypatch.setattr(budget, "hour_start", lambda now=None: original_hour(start))
    sent = []

    async def execute():
        dispatched = asyncio.Event()
        model = ChatModel(
            ModelConfig("custom", "test-only", "https://example.invalid", "test-only-token")
        )
        model.run_id = identifier
        await model.client.aclose()

        async def handler(request):
            sent.append(json.loads(request.content))
            dispatched.set()
            if mode == "cancel":
                await asyncio.sleep(10)
            if mode == "http_error":
                return httpx.Response(401, text="test-private-upstream-error")
            payload = {"choices": [{"message": {"role": "assistant", "content": "test"}}]}
            if mode in ("known", "invalid"):
                payload["usage"] = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
            if mode == "inconsistent":
                payload["usage"] = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 7}
            if mode == "malformed_usage":
                payload["usage"] = ["test-only-malformed-usage"]
            if mode == "invalid":
                payload["choices"] = []
            return httpx.Response(200, json=payload)

        model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            if mode == "cancel":
                task = asyncio.create_task(model.complete([{"role": "user", "content": "test"}]))
                await asyncio.wait_for(dispatched.wait(), timeout=3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif mode in ("invalid", "http_error"):
                with pytest.raises(ModelError):
                    await model.complete([{"role": "user", "content": "test"}])
            else:
                reply = await model.complete([{"role": "user", "content": "test"}])
                if mode in ("inconsistent", "malformed_usage"):
                    assert reply.usage == {}
        finally:
            await model.close()

    asyncio.run(execute())
    assert len(sent) == 1
    value = status(start)
    assert value["reserved"] == 0
    if mode in ("known", "invalid"):
        assert value["used"] == 7 and value["unknown_requests"] == 0
    else:
        assert value["used"] > 4096 and value["unknown_requests"] == 1
    with SessionLocal() as db:
        reservation = db.scalar(
            select(TokenReservation).where(TokenReservation.run_id == identifier)
        )
        assert reservation.state == "settled"
