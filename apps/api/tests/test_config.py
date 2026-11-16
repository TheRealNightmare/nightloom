import pytest
from pydantic import SecretStr, ValidationError

from nightloom_api.config import Settings, get_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NIGHTLOOM_ENV", "NIGHTLOOM_LOG_LEVEL", "NIGHTLOOM_API_PREFIX"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.env == "development"
    assert settings.log_level == "INFO"
    assert settings.api_prefix == "/api/v1"


# Non-placeholder secrets, so production settings validate.
REAL_SECRET_ENV = {
    "NIGHTLOOM_DATABASE_URL": "postgresql+asyncpg://u:real-pg-pw@db:5432/x",
    "NIGHTLOOM_NEO4J_PASSWORD": "real-neo4j-pw",
    "NIGHTLOOM_VALKEY_URL": "redis://:real-valkey-pw@cache:6379/0",
    "NIGHTLOOM_S3_ACCESS_KEY": "real-access-key",
    "NIGHTLOOM_S3_SECRET_KEY": "real-secret-key",
}


def use_real_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REAL_SECRET_ENV.items():
        monkeypatch.setenv(name, value)


def test_reads_nightloom_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    use_real_secrets(monkeypatch)
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


SECRET_FIELDS = (
    "database_url",
    "neo4j_password",
    "valkey_url",
    "s3_access_key",
    "s3_secret_key",
)
CONNECTION_VARS = (
    "NIGHTLOOM_DATABASE_URL",
    "NIGHTLOOM_NEO4J_URI",
    "NIGHTLOOM_NEO4J_USER",
    "NIGHTLOOM_NEO4J_PASSWORD",
    "NIGHTLOOM_VALKEY_URL",
    "NIGHTLOOM_S3_ENDPOINT",
    "NIGHTLOOM_S3_ACCESS_KEY",
    "NIGHTLOOM_S3_SECRET_KEY",
    "NIGHTLOOM_S3_BUCKET",
    "NIGHTLOOM_S3_REGION",
    "NIGHTLOOM_DEPENDENCY_TIMEOUT_SECONDS",
)


def test_connection_defaults_point_at_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONNECTION_VARS:
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert "@127.0.0.1:5432/" in settings.database_url.get_secret_value()
    assert settings.neo4j_uri == "bolt://127.0.0.1:7687"
    assert "@127.0.0.1:6379/" in settings.valkey_url.get_secret_value()
    assert settings.s3_endpoint == "http://127.0.0.1:8333"
    assert settings.s3_bucket == "nightloom-evidence"
    assert settings.s3_region == "us-east-1"
    assert settings.dependency_timeout_seconds == 2.0


def test_reads_connection_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", "postgresql+asyncpg://u:pw1@db:5432/x")
    monkeypatch.setenv("NIGHTLOOM_NEO4J_URI", "bolt://graph:7687")
    monkeypatch.setenv("NIGHTLOOM_NEO4J_PASSWORD", "pw2")
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", "redis://:pw3@cache:6379/1")
    monkeypatch.setenv("NIGHTLOOM_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("NIGHTLOOM_S3_SECRET_KEY", "sk")
    monkeypatch.setenv("NIGHTLOOM_DEPENDENCY_TIMEOUT_SECONDS", "0.5")

    settings = Settings()

    assert settings.database_url.get_secret_value() == "postgresql+asyncpg://u:pw1@db:5432/x"
    assert settings.neo4j_uri == "bolt://graph:7687"
    assert settings.neo4j_password.get_secret_value() == "pw2"
    assert settings.valkey_url.get_secret_value() == "redis://:pw3@cache:6379/1"
    assert settings.s3_access_key.get_secret_value() == "ak"
    assert settings.s3_secret_key.get_secret_value() == "sk"
    assert settings.dependency_timeout_seconds == 0.5


def test_secrets_are_hidden_in_repr_and_dumps() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://u:pg-pw@db/x"),
        neo4j_password=SecretStr("neo-pw"),
        valkey_url=SecretStr("redis://:vk-pw@cache/0"),
        s3_access_key=SecretStr("s3-ak"),
        s3_secret_key=SecretStr("s3-sk"),
    )

    rendered = [repr(settings), str(settings), settings.model_dump_json()]
    for text in rendered:
        for secret in ("pg-pw", "neo-pw", "vk-pw", "s3-ak", "s3-sk", "://u:"):
            assert secret not in text
    for name in SECRET_FIELDS:
        assert isinstance(getattr(settings, name), SecretStr)


def test_secret_values_include_url_passwords() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://u:p%40ss@db/x"),
        neo4j_password=SecretStr("neo-pw"),
        valkey_url=SecretStr("redis://cache/0"),
        s3_access_key=SecretStr("ak"),
        s3_secret_key=SecretStr("sk"),
    )

    assert set(settings.secret_values()) == {
        "postgresql+asyncpg://u:p%40ss@db/x",
        "p%40ss",
        "p@ss",
        "redis://cache/0",
        "neo-pw",
        "ak",
        "sk",
    }


def test_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(dependency_timeout_seconds=0)


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIGHTLOOM_ENV", "test")

    first = get_settings()

    assert first is get_settings()
    assert first.env == "test"


def test_production_refuses_placeholder_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONNECTION_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NIGHTLOOM_ENV", "production")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    message = str(excinfo.value)
    assert "Refusing to start with NIGHTLOOM_ENV=production" in message
    for name in (
        "NIGHTLOOM_DATABASE_URL",
        "NIGHTLOOM_NEO4J_PASSWORD",
        "NIGHTLOOM_VALKEY_URL",
        "NIGHTLOOM_S3_ACCESS_KEY",
        "NIGHTLOOM_S3_SECRET_KEY",
    ):
        assert name in message
    # Names only: no value (or part of one) is printed.
    assert "change-me-" not in message
    assert "postgres-password" not in message
    assert "127.0.0.1" not in message


def test_production_names_the_single_remaining_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_real_secrets(monkeypatch)
    monkeypatch.setenv("NIGHTLOOM_ENV", "production")
    monkeypatch.setenv("NIGHTLOOM_S3_SECRET_KEY", "change-me-s3-secret-key")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    message = str(excinfo.value)
    assert "NIGHTLOOM_S3_SECRET_KEY still contains the placeholder 'change-me'" in message
    assert "NIGHTLOOM_DATABASE_URL" not in message
    assert "real-" not in message


def test_production_accepts_real_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    use_real_secrets(monkeypatch)
    monkeypatch.setenv("NIGHTLOOM_ENV", "production")

    assert Settings().env == "production"


@pytest.mark.parametrize("env", ["development", "test"])
def test_placeholders_are_fine_outside_production(env: str) -> None:
    assert Settings(env=env).env == env  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("env", "cookie_secure", "expected"),
    [
        ("development", False, False),
        ("test", False, False),
        ("development", True, True),
        ("production", False, True),
    ],
)
def test_cookies_secure(
    monkeypatch: pytest.MonkeyPatch, env: str, cookie_secure: bool, expected: bool
) -> None:
    use_real_secrets(monkeypatch)
    monkeypatch.setenv("NIGHTLOOM_ENV", env)
    monkeypatch.setenv("NIGHTLOOM_COOKIE_SECURE", str(cookie_secure).lower())

    assert Settings().cookies_secure is expected
