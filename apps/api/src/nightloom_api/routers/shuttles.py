"""The installed Shuttles, as metadata."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from nightloom_api.auth.deps import CurrentUser
from nightloom_api.jobs.schemas import ShuttleOut
from nightloom_api.jobs.service import get_shuttle_registry
from nightloom_core.entities import EntityType
from nightloom_sdk.registry import ShuttleRegistry

router = APIRouter(prefix="/shuttles", tags=["shuttles"])


@router.get("", response_model=list[ShuttleOut])
async def list_shuttles(
    _user: CurrentUser,
    registry: Annotated[ShuttleRegistry, Depends(get_shuttle_registry)],
    input_type: Annotated[EntityType | None, Query()] = None,
) -> list[ShuttleOut]:
    """Installed Shuttles by name (any signed-in user); ``input_type`` keeps those accepting it.

    Lists every installed Shuttle, including ones a case may refuse (active
    or person-related): whether one can run is decided per case when a job
    is created.
    """
    return [ShuttleOut.build(shuttle.meta) for shuttle in registry.list(input_type)]
