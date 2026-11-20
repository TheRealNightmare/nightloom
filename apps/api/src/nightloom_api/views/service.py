"""Reading and writing saved views."""

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from nightloom_api.auth import clock
from nightloom_api.auth.models import User
from nightloom_api.views.models import SavedView
from nightloom_api.views.schemas import Author, ViewOut, ViewSummary

_Creator = aliased(User)
_Updater = aliased(User)


def _with_authors() -> Select[SavedView, User, User]:
    return (
        select(SavedView, _Creator, _Updater)
        .outerjoin(_Creator, _Creator.id == SavedView.created_by)
        .outerjoin(_Updater, _Updater.id == SavedView.updated_by)
    )


def _author(user: User | None) -> Author | None:
    return None if user is None else Author(id=user.id, display_name=user.display_name)


def summary(view: SavedView, creator: User | None, updater: User | None) -> ViewSummary:
    return ViewSummary(
        id=view.id,
        name=view.name,
        created_by=_author(creator),
        updated_by=_author(updater),
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def full(
    view: SavedView, creator: User | None, updater: User | None, state: dict[str, Any]
) -> ViewOut:
    return ViewOut(**summary(view, creator, updater).model_dump(), state=state)


async def list_views(db: AsyncSession, case_id: uuid.UUID) -> list[ViewSummary]:
    """The case's views by name (ignoring case)."""
    rows = await db.execute(
        _with_authors()
        .where(SavedView.case_id == case_id)
        .order_by(func.lower(SavedView.name), SavedView.id)
    )
    return [summary(view, creator, updater) for view, creator, updater in rows]


async def get_view(
    db: AsyncSession, case_id: uuid.UUID, view_id: uuid.UUID
) -> tuple[SavedView, User | None, User | None] | None:
    """The view with its authors, if it belongs to the case."""
    row = (
        await db.execute(
            _with_authors().where(SavedView.id == view_id, SavedView.case_id == case_id)
        )
    ).first()
    return None if row is None else (row[0], row[1], row[2])


async def count_views(db: AsyncSession, case_id: uuid.UUID) -> int:
    return int(await db.scalar(select(func.count()).where(SavedView.case_id == case_id)) or 0)


async def name_taken(
    db: AsyncSession, case_id: uuid.UUID, name: str, *, except_id: uuid.UUID | None = None
) -> bool:
    """Whether another view of the case has this name (ignoring case)."""
    statement = select(SavedView.id).where(
        SavedView.case_id == case_id, func.lower(SavedView.name) == name.lower()
    )
    if except_id is not None:
        statement = statement.where(SavedView.id != except_id)
    return await db.scalar(statement.limit(1)) is not None


async def create_view(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    state: dict[str, Any],
) -> SavedView:
    now = clock.utcnow()
    view = SavedView(
        case_id=case_id,
        name=name,
        state=state,
        created_by=user_id,
        updated_by=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(view)
    await db.flush()
    return view


def update_view(
    view: SavedView, *, user_id: uuid.UUID, name: str | None, state: dict[str, Any] | None
) -> None:
    if name is not None:
        view.name = name
    if state is not None:
        view.state = state
    view.updated_by = user_id
    view.updated_at = clock.utcnow()
