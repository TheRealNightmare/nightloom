"""Saved graph views.

Creates ``saved_views``: named snapshots of how a case's graph looks (camera,
filters, expanded groups, pinned positions), shared with the case's members.
Names are unique per case ignoring case. Deleted with the case; the author
columns are cleared when the user is deleted.

Revision ID: 0008_saved_views
Revises: 0007_graph_layouts
Create Date: 2026-11-21 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_saved_views"
down_revision: str | Sequence[str] | None = "0007_graph_layouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_saved_views_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_saved_views_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_saved_views_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_views")),
    )
    op.create_index(op.f("ix_saved_views_case_id"), "saved_views", ["case_id"], unique=False)
    op.create_index(
        "uq_saved_views_case_id_lower_name",
        "saved_views",
        ["case_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_saved_views_case_id_lower_name", table_name="saved_views")
    op.drop_index(op.f("ix_saved_views_case_id"), table_name="saved_views")
    op.drop_table("saved_views")
