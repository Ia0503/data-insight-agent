"""Add dataset mappings and versioned document indexes without changing workspace tables."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_analysis"
down_revision = "0001_workspace"


def upgrade():
    op.create_table(
        "dataset_mappings",
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("data_sources.id"), primary_key=True),
        sa.Column("fields", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Uuid(), nullable=False),
    )
    op.create_table(
        "document_indexes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("profile", JSONB(), nullable=False),
        sa.Column("options", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','ready','failed')", name="index_status"
        ),
    )
    op.create_index("ix_document_indexes_project_id", "document_indexes", ["project_id"])
    op.create_index("ix_document_indexes_source_id", "document_indexes", ["source_id"])
    op.create_index(
        "uq_active_source_index",
        "document_indexes",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "index_id",
            sa.Uuid(),
            sa.ForeignKey("document_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("record", sa.Integer()),
        sa.Column("record_id", sa.Text()),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
    )
    op.create_index("ix_document_chunks_index_id", "document_chunks", ["index_id"])
    op.create_index("uq_index_ordinal", "document_chunks", ["index_id", "ordinal"], unique=True)


def downgrade():
    op.drop_table("document_chunks")
    op.drop_table("document_indexes")
    op.drop_table("dataset_mappings")
