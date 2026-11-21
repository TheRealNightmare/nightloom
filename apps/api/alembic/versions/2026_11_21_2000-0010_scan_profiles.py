"""Scan profiles and scan runs.

Creates ``scan_profiles`` (profiles admins define; the built-in ones live in
code) and ``scan_runs`` (one profile run on one or more entities of a case,
deleted with the case, whose planned jobs are released as the requester's
earlier jobs finish), and adds ``jobs.run_id``.

Revision ID: 0010_scan_profiles
Revises: 0009_result_cache
Create Date: 2026-11-21 20:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_scan_profiles"
down_revision: str | Sequence[str] | None = "0009_result_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("shuttles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_scan_profiles_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_profiles")),
    )
    op.create_index(
        "uq_scan_profiles_lower_name",
        "scan_profiles",
        [sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("profile_name", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("released", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("entities", sa.Integer(), nullable=False),
        sa.Column("use_cache", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_scan_runs_finished_at_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'finished', 'cancelled', 'stopped')",
            name=op.f("ck_scan_runs_status"),
        ),
        sa.CheckConstraint(
            "released >= 0 AND skipped >= 0 AND entities >= 1", name=op.f("ck_scan_runs_counts")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_scan_runs_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_scan_runs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_runs")),
    )
    op.create_index(
        "ix_scan_runs_case_id_created_at_id",
        "scan_runs",
        ["case_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_scan_runs_requested_by_running",
        "scan_runs",
        ["requested_by"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.add_column("jobs", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_jobs_run_id"), "jobs", ["run_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_jobs_run_id_scan_runs"),
        "jobs",
        "scan_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_jobs_run_id_scan_runs"), "jobs", type_="foreignkey")
    op.drop_index(op.f("ix_jobs_run_id"), table_name="jobs")
    op.drop_column("jobs", "run_id")
    op.drop_index(
        "ix_scan_runs_requested_by_running",
        table_name="scan_runs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index("ix_scan_runs_case_id_created_at_id", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_index("uq_scan_profiles_lower_name", table_name="scan_profiles")
    op.drop_table("scan_profiles")
