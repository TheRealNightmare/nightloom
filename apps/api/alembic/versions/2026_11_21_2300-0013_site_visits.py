"""Website visits.

Adds ``cases.allow_site_visits``: owners allow Shuttles that read sites'
public pages, like a browser (``mode=visit``).

Revision ID: 0013_site_visits
Revises: 0012_ownership_verification
Create Date: 2026-11-21 23:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_site_visits"
down_revision: str | Sequence[str] | None = "0012_ownership_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column(
            "allow_site_visits", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("cases", "allow_site_visits")
