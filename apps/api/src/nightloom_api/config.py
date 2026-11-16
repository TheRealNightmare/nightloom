"""Application settings, read from ``NIGHTLOOM_*`` environment variables."""

from functools import lru_cache
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration for the API.

    The connection defaults point at the local stack's published ports on
    127.0.0.1 with the placeholder credentials from ``.env.example``, so the API
    and its unit tests run without any configuration. Passwords, keys and URLs
    that embed a password are ``SecretStr``: they never appear in ``repr()`` or
    logs; call ``.get_secret_value()`` only where the raw value is needed.
    """

    model_config = SettingsConfigDict(env_prefix="NIGHTLOOM_", extra="ignore")

    env: Environment = "development"
    log_level: LogLevel = "INFO"
    api_prefix: str = "/api/v1"

    # --- PostgreSQL (SQLAlchemy URL with the asyncpg driver) ---
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://nightloom:change-me-postgres-password@127.0.0.1:5432/nightloom"
    )

    # --- Neo4j ---
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("change-me-neo4j-password")

    # --- Valkey (Redis protocol) ---
    valkey_url: SecretStr = SecretStr("redis://:change-me-valkey-password@127.0.0.1:6379/0")

    # --- S3-compatible object storage ---
    s3_endpoint: str = "http://127.0.0.1:8333"
    s3_access_key: SecretStr = SecretStr("change-me-s3-access-key")
    s3_secret_key: SecretStr = SecretStr("change-me-s3-secret-key")
    s3_bucket: str = "nightloom-evidence"
    s3_region: str = "us-east-1"

    # Upper bound for each readiness check (seconds).
    dependency_timeout_seconds: float = Field(default=2.0, gt=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    def secret_values(self) -> list[str]:
        """Every secret string in these settings, for redacting text before it is shown.

        Includes the passwords embedded in connection URLs (raw and
        percent-decoded) as well as the URLs themselves.
        """
        values: set[str] = {
            self.neo4j_password.get_secret_value(),
            self.s3_access_key.get_secret_value(),
            self.s3_secret_key.get_secret_value(),
        }
        for url in (self.database_url, self.valkey_url):
            raw = url.get_secret_value()
            values.add(raw)
            password = urlsplit(raw).password
            if password:
                values.update({password, unquote(password)})
        return [value for value in values if value]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached; use as a FastAPI dependency)."""
    return Settings()
