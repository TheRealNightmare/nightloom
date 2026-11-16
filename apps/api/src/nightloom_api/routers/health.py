"""Health check endpoint."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from nightloom_api import __version__
from nightloom_api.config import Environment, Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness information about the running API."""

    status: Literal["ok"]
    version: str
    env: Environment


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report that the API is up, with its version and environment."""
    return HealthResponse(status="ok", version=__version__, env=settings.env)
