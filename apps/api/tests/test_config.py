import pytest
from pydantic import ValidationError

from nightloom_api.config import Settings, get_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NIGHTLOOM_ENV", "NIGHTLOOM_LOG_LEVEL", "NIGHTLOOM_API_PREFIX"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.env == "development"
    assert settings.log_level == "INFO"
    assert settings.api_prefix == "/api/v1"


def test_reads_nightloom_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHTLOOM_ENV", "production")
    monkeypatch.setenv("NIGHTLOOM_LOG_LEVEL", "debug")
    monkeypatch.setenv("NIGHTLOOM_API_PREFIX", "/api/v2")

    settings = Settings()

    assert settings.env == "production"
    assert settings.log_level == "DEBUG"
    assert settings.api_prefix == "/api/v2"


def test_rejects_unknown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHTLOOM_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHTLOOM_ENV", "test")

    first = get_settings()

    assert first is get_settings()
    assert first.env == "test"
