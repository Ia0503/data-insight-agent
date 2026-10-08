"""A shared UTC+8 natural-hour budget, reserved before each upstream request."""

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.agent.model import ModelError, safe_usage
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.call_protection import TokenHour, TokenReservation
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_OUTPUT_TOKENS = 4096
TOKEN_OVERHEAD = 2048


class QuotaError(ModelError):
    pass


def hour_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Quota timestamps must have a timezone.")
    return (
        now.astimezone(BUSINESS_TIMEZONE)
        .replace(minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )


def request_bound(body: dict) -> int:
    # UTF-8 字节上界覆盖中文、多轮上下文和工具定义；输出由请求 max_tokens 限制。
    # 不使用字符/4估算，以免中文低估。服务商异常用量仍按实际记录并阻止继续请求。
    return len(json.dumps(body, ensure_ascii=False).encode()) + MAX_OUTPUT_TOKENS + TOKEN_OVERHEAD


def reported_tokens(usage: dict | None) -> int | None:
    # 任务展示与额度账本使用同一规则，不能把矛盾的计数标记为完整用量。
    return safe_usage(usage).get("total_tokens")


def quota_status(db, now: datetime | None = None) -> dict:
    start = hour_start(now)
    hour = db.get(TokenHour, start)
    used, reserved = (hour.used_tokens, hour.reserved_tokens) if hour else (0, 0)
    limit = get_settings().llm_hourly_token_limit
    blocked = bool(hour and hour.blocked)
    return {
        "limit": limit,
        "used": used,
        "reserved": reserved,
        "remaining": 0 if blocked else max(0, limit - used - reserved),
        "can_start": not blocked and limit - used - reserved > MAX_OUTPUT_TOKENS + TOKEN_OVERHEAD,
        "blocked": blocked,
        "unknown_requests": hour.unknown_requests if hour else 0,
        "reset_at": (start + timedelta(hours=1)).isoformat(),
        "timezone": "Asia/Shanghai",
    }


def ensure_available(db):
    if quota_status(db)["remaining"] <= MAX_OUTPUT_TOKENS + TOKEN_OVERHEAD:
        raise QuotaError("本小时模型 Token 额度不足，请在中国标准时间下一个整点后重试。")


def reserve_tokens(run_id: UUID, amount: int, now: datetime | None = None) -> UUID:
    if type(amount) is not int or amount <= 0:
        raise ValueError("Invalid reservation amount.")
    start = hour_start(now)
    with SessionLocal() as db:
        db.execute(insert(TokenHour).values(hour_start=start).on_conflict_do_nothing())
        hour = db.scalar(select(TokenHour).where(TokenHour.hour_start == start).with_for_update())
        if (
            hour.blocked
            or hour.used_tokens + hour.reserved_tokens + amount
            > get_settings().llm_hourly_token_limit
        ):
            raise QuotaError(
                "本小时模型 Token 额度不足以预留本次请求，请缩小问题或等待下一个整点。"
            )
        identifier = uuid4()
        db.add(TokenReservation(id=identifier, hour_start=start, run_id=run_id, amount=amount))
        hour.reserved_tokens += amount
        db.commit()
    logger.info("event=model_budget_reserved run_id=%s amount=%s", run_id, amount)
    return identifier


def _charge(hour, reservation, total: int | None):
    if reservation.state == "settled":
        return False
    charged = reservation.amount if total is None else total
    hour.reserved_tokens -= reservation.amount
    hour.used_tokens += charged
    hour.unknown_requests += int(total is None)
    reservation.charged_tokens = charged
    reservation.usage_known = total is not None
    reservation.state = "settled"
    reservation.settled_at = datetime.now(timezone.utc)
    if charged > reservation.amount:
        hour.blocked = True
        logger.error(
            "event=model_budget_bound_exceeded run_id=%s reserved=%s charged=%s",
            reservation.run_id,
            reservation.amount,
            charged,
        )
        return True
    return False


def settle_tokens(identifier: UUID, usage: dict | None):
    with SessionLocal() as db:
        reservation = db.get(TokenReservation, identifier)
        if reservation is None:
            raise RuntimeError("Reservation missing; refuse to release budget.")
        # 所有核销/恢复均先锁小时，再锁预留记录，重复收尾不会重复扣减。
        hour = db.scalar(
            select(TokenHour)
            .where(TokenHour.hour_start == reservation.hour_start)
            .with_for_update()
        )
        reservation = db.scalar(
            select(TokenReservation)
            .where(TokenReservation.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        exceeded = _charge(hour, reservation, reported_tokens(usage))
        db.commit()
    if exceeded:
        raise QuotaError("服务商用量超过预留范围，本小时后续模型请求已暂停，请核对配置。")


def settle_unfinished(db, run_id: UUID | None = None) -> int:
    condition = [TokenReservation.state == "reserved"]
    if run_id is not None:
        condition.append(TokenReservation.run_id == run_id)
    starts = db.scalars(
        select(TokenReservation.hour_start)
        .where(*condition)
        .distinct()
        .order_by(TokenReservation.hour_start)
    ).all()
    count = 0
    for start in starts:
        hour = db.scalar(select(TokenHour).where(TokenHour.hour_start == start).with_for_update())
        reservations = db.scalars(
            select(TokenReservation)
            .where(*condition, TokenReservation.hour_start == start)
            .with_for_update()
        ).all()
        for reservation in reservations:
            _charge(hour, reservation, None)
            count += 1
    return count
