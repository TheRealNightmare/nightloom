"""Personal graph layouts.

Creates ``graph_layouts``: one row per user and case holding the entities
that user pinned in the case's graph, as a JSON map of entity key to
``[x, y]``. Deleted with the case or the user. Rows are a UI preference and
are not audited.

Revision ID: 0007_graph_layouts
Revises: 0006_must_change_password
Create Date: 2026-11-20 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_graph_layouts"
down_revision: str | Sequence[str] | None = "0006_must_change_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_layouts",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "positions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
            name=op.f("fk_graph_layouts_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_graph_layouts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_id", "user_id", name=op.f("pk_graph_layouts")),
    )
    op.create_index(op.f("ix_graph_layouts_user_id"), "graph_layouts", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_graph_layouts_user_id"), table_name="graph_layouts")
    op.drop_table("graph_layouts")
