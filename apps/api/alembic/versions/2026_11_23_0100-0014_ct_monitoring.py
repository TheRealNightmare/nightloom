"""Certificate Transparency monitoring.

Adds ``ct_watches`` (domains a case's owners watch for new certificates,
checked hourly by the worker with a crt.sh job) and ``ct_alerts`` (the new
certificates and names a check added, until a member marks them seen).

Revision ID: 0014_ct_monitoring
Revises: 0013_site_visits
Create Date: 2026-11-23 01:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_ct_monitoring"
down_revision: str | Sequence[str] | None = "0013_site_visits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ct_watches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("pending_job_id", sa.Uuid(), nullable=True),
        sa.Column("last_job_id", sa.Uuid(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_problem", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_ct_watches_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_ct_watches_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_job_id"],
            ["jobs.id"],
            name=op.f("fk_ct_watches_last_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["pending_job_id"],
            ["jobs.id"],
            name=op.f("fk_ct_watches_pending_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ct_watches")),
        sa.UniqueConstraint("case_id", "domain", name="uq_ct_watches_case_domain"),
    )
    op.create_index(op.f("ix_ct_watches_case_id"), "ct_watches", ["case_id"], unique=False)
    op.create_index("ix_ct_watches_next_check_at", "ct_watches", ["next_check_at"], unique=False)
    op.create_table(
        "ct_alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("watch_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("certificates", sa.Integer(), nullable=False),
        sa.Column(
            "names",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("names_total", sa.Integer(), nullable=False),
        sa.Column(
            "keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "certificates >= 0 AND names_total >= 0", name=op.f("ck_ct_alerts_counts")
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"],
            ["users.id"],
            name=op.f("fk_ct_alerts_acknowledged_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_ct_alerts_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_ct_alerts_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["watch_id"],
            ["ct_watches.id"],
            name=op.f("fk_ct_alerts_watch_id_ct_watches"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ct_alerts")),
    )
    op.create_index(
        "ix_ct_alerts_case_id_created_at", "ct_alerts", ["case_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_ct_alerts_unacknowledged",
        "ct_alerts",
        ["case_id"],
        unique=False,
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ct_alerts_unacknowledged",
        table_name="ct_alerts",
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )
    op.drop_index("ix_ct_alerts_case_id_created_at", table_name="ct_alerts")
    op.drop_table("ct_alerts")
    op.drop_index("ix_ct_watches_next_check_at", table_name="ct_watches")
    op.drop_index(op.f("ix_ct_watches_case_id"), table_name="ct_watches")
    op.drop_table("ct_watches")
