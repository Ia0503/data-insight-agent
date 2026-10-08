"""Persist failed attempts so restarting the API does not reset password limits."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.core.access import parse_hash, verify_password
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.call_protection import AccessAttempt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)
WINDOW = timedelta(minutes=15)
MAX_FAILURES = 6


def authorize_new_run(password: str | None, client: str):
    settings = get_settings()
    if not settings.analysis_password_required:
        return
    encoded = settings.analysis_password_hash.get_secret_value()
    if parse_hash(encoded) is None:
        raise HTTPException(503, "调用密码未配置，请在本地运行密码配置脚本。")
    if not password:
        raise HTTPException(401, "每次新建智能分析都需要输入调用密码。")
    # 只存客户端摘要，日志不记录地址、密码、密码摘要或请求头。
    key = hashlib.sha256(("newai-call-access:" + client).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(insert(AccessAttempt).values(key=key, window_start=now).on_conflict_do_nothing())
        attempt = db.scalar(select(AccessAttempt).where(AccessAttempt.key == key).with_for_update())
        if attempt.blocked_until and attempt.blocked_until > now:
            retry = max(1, int((attempt.blocked_until - now).total_seconds()))
            raise HTTPException(
                429, "密码错误次数过多，请稍后重试。", headers={"Retry-After": str(retry)}
            )
        if now - attempt.window_start >= WINDOW:
            attempt.window_start, attempt.failures, attempt.blocked_until = now, 0, None
        accepted = verify_password(password, encoded)
        if accepted:
            attempt.failures, attempt.blocked_until = 0, None
        else:
            attempt.failures += 1
            if attempt.failures >= MAX_FAILURES:
                attempt.blocked_until = now + WINDOW
        db.commit()
    if not accepted:
        logger.warning("event=analysis_access_rejected")
        raise HTTPException(401, "调用密码不正确，请重新输入。")
