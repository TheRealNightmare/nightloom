"""Reading and replacing a user's layout in a case."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.auth import clock
from nightloom_api.layouts.models import GraphLayout


async def get_layout(
    db: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID
) -> GraphLayout | None:
    return await db.scalar(
        select(GraphLayout).where(GraphLayout.case_id == case_id, GraphLayout.user_id == user_id)
    )


async def replace_layout(
    db: AsyncSession, case_id: uuid.UUID, user_id: uuid.UUID, positions: dict[str, Any]
) -> None:
    """Store ``positions`` as the user's layout in the case (one statement, no read first)."""
    now = clock.utcnow()
    statement = insert(GraphLayout).values(
        case_id=case_id, user_id=user_id, positions=positions, updated_at=now
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[GraphLayout.case_id, GraphLayout.user_id],
            set_={"positions": statement.excluded.positions, "updated_at": now},
        )
    )
