"""Application settings, read from ``NIGHTLOOM_*`` environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration for the API."""

    model_config = SettingsConfigDict(env_prefix="NIGHTLOOM_", extra="ignore")

    env: Environment = "development"
    log_level: LogLevel = "INFO"
    api_prefix: str = "/api/v1"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached; use as a FastAPI dependency)."""
    return Settings()
