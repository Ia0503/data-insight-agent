from datetime import datetime
from uuid import UUID, uuid4

from app.core.database import Base
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class DatasetMapping(Base):
    __tablename__ = "dataset_mappings"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id"), primary_key=True)
    fields: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[UUID] = mapped_column(default=uuid4)


class DocumentIndex(Base):
    __tablename__ = "document_indexes"
    __table_args__ = (
        CheckConstraint("status IN ('queued','processing','ready','failed')", name="index_status"),
        Index("uq_active_source_index", "source_id", unique=True, postgresql_where=text("active")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_sources.id"), index=True)
    signature: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    profile: Mapped[dict] = mapped_column(JSONB)
    options: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    active: Mapped[bool] = mapped_column(default=False)
    chunk_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("uq_index_ordinal", "index_id", "ordinal", unique=True),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    index_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_indexes.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int]
    page: Mapped[int | None] = mapped_column(nullable=True)
    record: Mapped[int | None] = mapped_column(nullable=True)
    record_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    start: Mapped[int]
    end: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    # 不固定列维度，支持保留不同模型的旧索引；搜索先按模型版本隔离再计算距离。
    embedding: Mapped[list[float]] = mapped_column(Vector())
