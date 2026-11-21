"""Auto-pivot.

Adds ``cases.allow_auto_pivot`` (owners turn it on), the pivot settings and
progress of ``scan_runs`` (``pivot``, ``max_depth``, ``max_jobs``,
``max_entities``, ``seen``, ``discovered``, ``capped``) with the profile's
Shuttles at the start (``profile_shuttles``), and ``jobs.pivot_depth`` /
``jobs.pivot_expanded``.

Revision ID: 0011_auto_pivot
Revises: 0010_scan_profiles
Create Date: 2026-11-21 21:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_auto_pivot"
down_revision: str | Sequence[str] | None = "0010_scan_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column(
            "allow_auto_pivot", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column("jobs", sa.Column("pivot_depth", sa.Integer(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("pivot_expanded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "scan_runs",
        sa.Column(
            "profile_shuttles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column("pivot", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "scan_runs",
        sa.Column("max_depth", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "scan_runs",
        sa.Column("max_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "scan_runs",
        sa.Column("max_entities", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "scan_runs",
        sa.Column(
            "seen",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column("discovered", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("scan_runs", sa.Column("capped", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_runs", "capped")
    op.drop_column("scan_runs", "discovered")
    op.drop_column("scan_runs", "seen")
    op.drop_column("scan_runs", "max_entities")
    op.drop_column("scan_runs", "max_jobs")
    op.drop_column("scan_runs", "max_depth")
    op.drop_column("scan_runs", "pivot")
    op.drop_column("scan_runs", "profile_shuttles")
    op.drop_column("jobs", "pivot_expanded")
    op.drop_column("jobs", "pivot_depth")
    op.drop_column("cases", "allow_auto_pivot")
