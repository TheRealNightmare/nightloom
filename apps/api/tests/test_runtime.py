import importlib.metadata
import logging
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

import nightloom_api
from nightloom_api import __main__ as entrypoint
from nightloom_api.logging import configure_logging


def test_get_version_falls_back_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(nightloom_api, "version", missing)

    assert nightloom_api.get_version() == "0.0.0"


@pytest.fixture
def root_logger() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_configure_logging_sets_level_and_single_handler(root_logger: logging.Logger) -> None:
    # Cleared here rather than in the fixture: pytest attaches its capture
    # handlers to the root logger after fixture setup.
    root_logger.handlers.clear()

    configure_logging("WARNING")
    configure_logging("DEBUG")

    assert root_logger.level == logging.DEBUG
    assert len(root_logger.handlers) == 1


BLOCK_STUB_PACKAGES = """
import importlib.abc, sys

class BlockStubs(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0].startswith("types_") or name.endswith("_stubs"):
            raise ModuleNotFoundError(f"dev-only stub package imported: {name}")
        return None

sys.meta_path.insert(0, BlockStubs())
import nightloom_api.main
"""


def test_app_imports_without_dev_only_stub_packages() -> None:
    # Stub packages (types-aiobotocore, ...) are dev dependencies and absent
    # from the runtime image; they may only be imported under TYPE_CHECKING.
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", BLOCK_STUB_PACKAGES],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("env", "expected_reload"),
    [("development", True), ("test", False), ("production", False)],
)
def test_main_runs_uvicorn(
    monkeypatch: pytest.MonkeyPatch, env: str, expected_reload: bool
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setenv("NIGHTLOOM_ENV", env)
    # Production refuses the built-in placeholder secrets.
    monkeypatch.setenv("NIGHTLOOM_DATABASE_URL", "postgresql+asyncpg://u:real-pw@db/x")
    monkeypatch.setenv("NIGHTLOOM_NEO4J_PASSWORD", "real-pw")
    monkeypatch.setenv("NIGHTLOOM_VALKEY_URL", "redis://:real-pw@cache/0")
    monkeypatch.setenv("NIGHTLOOM_S3_ACCESS_KEY", "real-ak")
    monkeypatch.setenv("NIGHTLOOM_S3_SECRET_KEY", "real-sk")
    monkeypatch.delenv("NIGHTLOOM_LOG_LEVEL", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    entrypoint.main()

    assert calls == [
        (
            ("nightloom_api.main:app",),
            {"host": "127.0.0.1", "port": 8000, "reload": expected_reload, "log_level": "info"},
        )
    ]
