"""Ownership verification.

Adds ``ownership_verifications``: the domains a case's owners verify (DNS TXT
record or a file on the website), with their status, expiry and the
addresses they resolved to, so active Shuttles may run on what they cover.

Revision ID: 0012_ownership_verification
Revises: 0011_auto_pivot
Create Date: 2026-11-21 22:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_ownership_verification"
down_revision: str | Sequence[str] | None = "0011_auto_pivot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ownership_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("check_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_renews", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "addresses",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "method IN ('dns_txt', 'http_file')", name=op.f("ck_ownership_verifications_method")
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR (verified_at IS NOT NULL AND expires_at IS NOT NULL)",
            name=op.f("ck_ownership_verifications_verified_has_dates"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'failed', 'revoked', 'expired')",
            name=op.f("ck_ownership_verifications_status"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_ownership_verifications_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_ownership_verifications_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ownership_verifications")),
        sa.UniqueConstraint("case_id", "domain", name="uq_ownership_verifications_case_domain"),
    )
    op.create_index(
        op.f("ix_ownership_verifications_case_id"),
        "ownership_verifications",
        ["case_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ownership_verifications_case_id"), table_name="ownership_verifications")
    op.drop_table("ownership_verifications")
