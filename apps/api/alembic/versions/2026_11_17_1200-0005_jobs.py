"""Shuttle jobs.

Creates ``jobs`` (one Shuttle run on one entity of a case, deleted with the
case; the requester is set to NULL when their user is deleted) and the enum
type ``job_status``. ``finished_at`` is set exactly when the job is in a
final state, and ``error`` holds a short, user-safe message, never a raw
exception.

Revision ID: 0005_jobs
Revises: 0004_cases
Create Date: 2026-11-17 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_jobs"
down_revision: str | Sequence[str] | None = "0004_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

job_status = sa.Enum("queued", "running", "succeeded", "failed", "cancelled", name="job_status")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("shuttle_name", sa.Text(), nullable=False),
        sa.Column("shuttle_version", sa.Text(), nullable=False),
        sa.Column("input_key", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("status", job_status, server_default="queued", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint(
            "error IS NULL OR char_length(error) <= 500", name=op.f("ck_jobs_error_length")
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running')) = (finished_at IS NULL)",
            name=op.f("ck_jobs_finished_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_jobs_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_jobs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_case_id_created_at_id", "jobs", ["case_id", "created_at", "id"], unique=False
    )
    op.create_index(
        "ix_jobs_requested_by_active",
        "jobs",
        ["requested_by"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jobs_requested_by_active",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index("ix_jobs_case_id_created_at_id", table_name="jobs")
    op.drop_table("jobs")
    # drop_table does not remove enum types; without this, upgrading again fails.
    job_status.drop(op.get_bind(), checkfirst=True)
