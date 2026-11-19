"""Forced password change.

Adds ``users.must_change_password``. While it is true, the user can only read
their own account, change their password and sign out; every other route
answers ``403`` with the code ``password_change_required``. Admins set it when
they create a user with a temporary password or reset a password; changing
the password clears it. Existing users keep their passwords (``false``).

Revision ID: 0006_must_change_password
Revises: 0005_jobs
Create Date: 2026-11-19 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_must_change_password"
down_revision: str | Sequence[str] | None = "0005_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
