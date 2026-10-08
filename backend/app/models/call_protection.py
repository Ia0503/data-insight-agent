from datetime import datetime
from uuid import UUID, uuid4

from app.core.database import Base
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column


class TokenHour(Base):
    __tablename__ = "token_hours"
    __table_args__ = (
        CheckConstraint("used_tokens >= 0 AND reserved_tokens >= 0", name="token_hour_nonnegative"),
    )
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    unknown_requests: Mapped[int] = mapped_column(default=0, server_default="0")
    blocked: Mapped[bool] = mapped_column(default=False, server_default="false")


class TokenReservation(Base):
    __tablename__ = "token_reservations"
    __table_args__ = (
        CheckConstraint(
            "amount > 0 AND (charged_tokens IS NULL OR charged_tokens >= 0)",
            name="token_reservation_nonnegative",
        ),
        CheckConstraint("state IN ('reserved','settled')", name="token_reservation_state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    hour_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), ForeignKey("token_hours.hour_start"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    charged_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_known: Mapped[bool] = mapped_column(default=False)
    state: Mapped[str] = mapped_column(String(12), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccessAttempt(Base):
    __tablename__ = "access_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(default=0, server_default="0")
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
