"""scripts/generate-env.sh (`make secrets`): a production .env from .env.example.

Runs the real script on a copy of the repository's .env.example in a
temporary directory; needs bash and openssl (or python3).
"""

import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from nightloom_api.config import PLACEHOLDER_MARKER, Settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate-env.sh"
EXAMPLE = ROOT / ".env.example"
HEX_SECRET = re.compile(r"^[0-9a-f]{64}$")
BASH = shutil.which("bash") or "/bin/bash"
CURRENT_VERSION = json.loads((ROOT / "apps" / "web" / "package.json").read_text())["version"]


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed script, arguments from the test
        [
            BASH,
            str(SCRIPT),
            "--example",
            str(tmp_path / ".env.example"),
            "--output",
            str(tmp_path / ".env"),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            values[name] = value
    return values


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLE, tmp_path / ".env.example")
    return tmp_path


def test_creates_a_production_env_with_random_consistent_secrets(workdir: Path) -> None:
    result = run(workdir, "--domain", "nightloom.example.org", "--acme-email", "ops@example.org")

    assert result.returncode == 0, result.stderr
    env_file = workdir / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    text = env_file.read_text()
    assert PLACEHOLDER_MARKER not in "\n".join(
        line for line in text.splitlines() if not line.startswith("#")
    )
    env = read_env(env_file)

    for name in (
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "VALKEY_PASSWORD",
        "NIGHTLOOM_S3_ACCESS_KEY",
        "NIGHTLOOM_S3_SECRET_KEY",
    ):
        assert HEX_SECRET.match(env[name]), name
    secrets = [env["POSTGRES_PASSWORD"], env["NEO4J_PASSWORD"], env["VALKEY_PASSWORD"]]
    secrets += [env["NIGHTLOOM_S3_ACCESS_KEY"], env["NIGHTLOOM_S3_SECRET_KEY"]]
    assert len(set(secrets)) == len(secrets), "every placeholder gets its own value"

    # The connection settings repeat the passwords they belong to.
    assert env["NIGHTLOOM_DATABASE_URL"] == (
        f"postgresql+asyncpg://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}"
        f"@postgres:5432/{env['POSTGRES_DB']}"
    )
    assert env["NIGHTLOOM_NEO4J_PASSWORD"] == env["NEO4J_PASSWORD"]
    assert env["NIGHTLOOM_VALKEY_URL"] == f"redis://:{env['VALKEY_PASSWORD']}@valkey:6379/0"

    assert env["NIGHTLOOM_ENV"] == "production"
    assert env["NIGHTLOOM_COOKIE_SECURE"] == "true"
    assert env["NIGHTLOOM_EDGE"] == "caddy"
    assert env["COMPOSE_PROFILES"] == "https"
    assert env["NIGHTLOOM_DOMAIN"] == "nightloom.example.org"
    assert env["NIGHTLOOM_ACME_EMAIL"] == "ops@example.org"
    assert "NIGHTLOOM_SMALL_HOST" not in env
    # The release images to pull: this checkout's version.
    assert env["NIGHTLOOM_VERSION"] == CURRENT_VERSION

    # The API accepts the result in production (no placeholder left).
    settings = Settings(
        env="production",
        database_url=SecretStr(env["NIGHTLOOM_DATABASE_URL"]),
        neo4j_password=SecretStr(env["NIGHTLOOM_NEO4J_PASSWORD"]),
        valkey_url=SecretStr(env["NIGHTLOOM_VALKEY_URL"]),
        s3_access_key=SecretStr(env["NIGHTLOOM_S3_ACCESS_KEY"]),
        s3_secret_key=SecretStr(env["NIGHTLOOM_S3_SECRET_KEY"]),
    )
    assert settings.cookies_secure


def test_prints_the_generated_names_but_never_the_values(workdir: Path) -> None:
    result = run(workdir)

    assert result.returncode == 0, result.stderr
    env = read_env(workdir / ".env")
    for name in ("POSTGRES_PASSWORD", "NIGHTLOOM_DATABASE_URL", "NIGHTLOOM_S3_SECRET_KEY"):
        assert f"  {name}\n" in result.stdout
    output = result.stdout + result.stderr
    for name in (
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "VALKEY_PASSWORD",
        "NIGHTLOOM_S3_ACCESS_KEY",
        "NIGHTLOOM_S3_SECRET_KEY",
    ):
        assert env[name] not in output
    assert "NIGHTLOOM_DOMAIN and NIGHTLOOM_ACME_EMAIL" in result.stdout


def test_two_runs_generate_different_secrets(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for directory in (first, second):
        directory.mkdir()
        shutil.copy(EXAMPLE, directory / ".env.example")
        assert run(directory).returncode == 0

    assert (
        read_env(first / ".env")["POSTGRES_PASSWORD"]
        != read_env(second / ".env")["POSTGRES_PASSWORD"]
    )


def test_refuses_to_overwrite_an_existing_env(workdir: Path) -> None:
    existing = workdir / ".env"
    existing.write_text("POSTGRES_PASSWORD=keep-me\n")

    result = run(workdir)

    assert result.returncode == 1
    assert "not overwriting" in result.stderr
    assert existing.read_text() == "POSTGRES_PASSWORD=keep-me\n"
    assert list(workdir.glob(".env.backup-*")) == []


def test_force_backs_up_the_old_file_and_warns(workdir: Path) -> None:
    existing = workdir / ".env"
    existing.write_text("POSTGRES_PASSWORD=keep-me\n")

    result = run(workdir, "--force")

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    backups = list(workdir.glob(".env.backup-*"))
    assert len(backups) == 1
    assert re.fullmatch(r"\.env\.backup-\d{8}T\d{6}Z", backups[0].name)
    assert backups[0].read_text() == "POSTGRES_PASSWORD=keep-me\n"
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert HEX_SECRET.match(read_env(existing)["POSTGRES_PASSWORD"])


def test_cloudflared_edge_and_small_host(workdir: Path) -> None:
    result = run(workdir, "--edge", "cloudflared", "--small")

    assert result.returncode == 0, result.stderr
    env = read_env(workdir / ".env")
    assert env["NIGHTLOOM_EDGE"] == "cloudflared"
    assert env["COMPOSE_PROFILES"] == "tunnel"
    assert env["NIGHTLOOM_SMALL_HOST"] == "true"
    assert "TUNNEL_TOKEN" in result.stdout


def test_version_option_selects_another_release(workdir: Path) -> None:
    result = run(workdir, "--version", "v1.2.3-rc.4")

    assert result.returncode == 0, result.stderr
    assert read_env(workdir / ".env")["NIGHTLOOM_VERSION"] == "1.2.3-rc.4"
    assert "NIGHTLOOM_VERSION=1.2.3-rc.4" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("--version", "latest"),
        ("--version", "1.2"),
        ("--edge", "nginx"),
        ("--domain", "bad domain; rm -rf /"),
        ("--acme-email", "not an email"),
        ("--unknown",),
    ],
)
def test_rejects_bad_arguments_without_writing(workdir: Path, args: tuple[str, ...]) -> None:
    result = run(workdir, *args)

    assert result.returncode == 2
    assert not (workdir / ".env").exists()
