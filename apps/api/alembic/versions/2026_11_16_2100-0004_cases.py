"""Investigation cases and case membership.

Creates ``cases`` (mandatory purpose, legal basis and ethics acknowledgement,
with CHECK constraints mirroring the API's validation) and ``case_members``
(one role per user and case; rows go with the case or the user), plus the
enum types ``case_legal_basis``, ``case_status`` and ``case_member_role``.

``audit_events.case_id`` deliberately gets NO foreign key to ``cases``: the
audit trail must outlive a deleted case, and audit rows can never be changed
(a SET NULL or CASCADE would rewrite or remove hashed, append-only rows).

Revision ID: 0004_cases
Revises: 0003_audit_events
Create Date: 2026-11-16 21:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_cases"
down_revision: str | Sequence[str] | None = "0003_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

case_legal_basis = sa.Enum(
    "owned_assets",
    "authorized_engagement",
    "threat_intelligence",
    "journalism",
    "academic_research",
    "self_assessment",
    "other",
    name="case_legal_basis",
)
case_status = sa.Enum("open", "closed", "archived", name="case_status")
case_member_role = sa.Enum("owner", "editor", "viewer", name="case_member_role")


def upgrade() -> None:
    # create_table creates each enum type on first use.
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("legal_basis", case_legal_basis, nullable=False),
        sa.Column("legal_basis_note", sa.Text(), nullable=True),
        sa.Column("ethics_ack_version", sa.Text(), nullable=False),
        sa.Column("ethics_ack_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "allow_person_entities", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("status", case_status, server_default="open", nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
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
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 3 AND 200", name=op.f("ck_cases_title_length")
        ),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 5000",
            name=op.f("ck_cases_description_length"),
        ),
        sa.CheckConstraint(
            "char_length(purpose) BETWEEN 30 AND 2000", name=op.f("ck_cases_purpose_length")
        ),
        sa.CheckConstraint(
            "legal_basis_note IS NULL OR char_length(legal_basis_note) <= 1000",
            name=op.f("ck_cases_legal_basis_note_length"),
        ),
        sa.CheckConstraint(
            "legal_basis NOT IN ('authorized_engagement', 'other') "
            "OR btrim(coalesce(legal_basis_note, '')) <> ''",
            name=op.f("ck_cases_legal_basis_note_required"),
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN 1 AND 3650",
            name=op.f("ck_cases_retention_days_range"),
        ),
        sa.CheckConstraint(
            "(status <> 'closed' OR closed_at IS NOT NULL) "
            "AND (status <> 'open' OR closed_at IS NULL)",
            name=op.f("ck_cases_closed_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_cases_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
    )
    op.create_index("ix_cases_status_updated_at", "cases", ["status", "updated_at"], unique=False)

    op.create_table(
        "case_members",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", case_member_role, nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_members_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_case_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_case_members_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("case_id", "user_id", name=op.f("pk_case_members")),
    )
    op.create_index(op.f("ix_case_members_user_id"), "case_members", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_case_members_user_id"), table_name="case_members")
    op.drop_table("case_members")
    op.drop_index("ix_cases_status_updated_at", table_name="cases")
    op.drop_table("cases")
    # drop_table does not remove enum types; without this, upgrading again fails.
    bind = op.get_bind()
    for enum in (case_member_role, case_status, case_legal_basis):
        enum.drop(bind, checkfirst=True)
