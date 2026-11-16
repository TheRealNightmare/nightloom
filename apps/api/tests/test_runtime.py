import importlib.metadata
import logging
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


@pytest.mark.parametrize(
    ("env", "expected_reload"),
    [("development", True), ("test", False), ("production", False)],
)
def test_main_runs_uvicorn(
    monkeypatch: pytest.MonkeyPatch, env: str, expected_reload: bool
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setenv("NIGHTLOOM_ENV", env)
    monkeypatch.delenv("NIGHTLOOM_LOG_LEVEL", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    entrypoint.main()

    assert calls == [
        (
            ("nightloom_api.main:app",),
            {"host": "127.0.0.1", "port": 8000, "reload": expected_reload, "log_level": "info"},
        )
    ]
