"""Append-only, hash-chained audit log.

Creates ``audit_events`` and a trigger that raises on every UPDATE, DELETE and
TRUNCATE of it. ``case_id`` has no foreign key yet (the cases table comes
later). ``actor_user_id`` references users with ON DELETE RESTRICT: the row may
not change, so users with audit history are deactivated, never deleted.

Revision ID: 0003_audit_events
Revises: 0002_users_sessions
Create Date: 2026-11-16 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_audit_events"
down_revision: str | Sequence[str] | None = "0002_users_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_FUNCTION = """
CREATE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Audit events can only be inserted.';
END;
$$
"""

# Statement-level, so it also fires for statements that match no rows, and for
# TRUNCATE (which has no row-level triggers).
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.Text(), nullable=True),
        sa.Column("actor_ip", postgresql.INET(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("hash", sa.CHAR(length=64), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied')",
            name=op.f("ck_audit_events_outcome_valid"),
        ),
        sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_audit_events_hash_hex")),
        sa.CheckConstraint(
            "prev_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_audit_events_prev_hash_hex")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_events_hash")),
        sa.UniqueConstraint("prev_hash", name=op.f("uq_audit_events_prev_hash")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_index(
        "ix_audit_events_actor_user_id_occurred_at",
        "audit_events",
        ["actor_user_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_case_id_occurred_at",
        "audit_events",
        ["case_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_index("ix_audit_events_case_id_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_user_id_occurred_at", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
