"""Result cache for passive Shuttles.

Creates ``result_cache``: a successful run's raw findings, reusable for the
same Shuttle version and entity until they expire (per case by default, or
instance-wide). Case-scoped entries are deleted with their case. Adds
``jobs.use_cache`` (False for a fresh run), ``jobs.from_cache`` and
``jobs.cached_fetched_at``.

Revision ID: 0009_result_cache
Revises: 0008_saved_views
Create Date: 2026-11-21 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_result_cache"
down_revision: str | Sequence[str] | None = "0008_saved_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "result_cache",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("shuttle_name", sa.Text(), nullable=False),
        sa.Column("shuttle_version", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.Text(), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "notes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(scope = 'case') = (case_id IS NOT NULL)",
            name=op.f("ck_result_cache_case_id_matches_scope"),
        ),
        sa.CheckConstraint("scope IN ('case', 'instance')", name=op.f("ck_result_cache_scope")),
        sa.CheckConstraint(
            "size_bytes >= 0 AND finding_count >= 0 AND hits >= 0",
            name=op.f("ck_result_cache_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_result_cache_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_result_cache")),
    )
    op.create_index(op.f("ix_result_cache_case_id"), "result_cache", ["case_id"], unique=False)
    op.create_index("ix_result_cache_expires_at", "result_cache", ["expires_at"], unique=False)
    op.create_index("ix_result_cache_shuttle_name", "result_cache", ["shuttle_name"], unique=False)
    op.add_column(
        "jobs",
        sa.Column("use_cache", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("from_cache", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("jobs", sa.Column("cached_fetched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "cached_fetched_at")
    op.drop_column("jobs", "from_cache")
    op.drop_column("jobs", "use_cache")
    op.drop_index("ix_result_cache_shuttle_name", table_name="result_cache")
    op.drop_index("ix_result_cache_expires_at", table_name="result_cache")
    op.drop_index(op.f("ix_result_cache_case_id"), table_name="result_cache")
    op.drop_table("result_cache")
