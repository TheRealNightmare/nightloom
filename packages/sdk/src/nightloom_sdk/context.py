"""What a running Shuttle can use: :class:`ShuttleContext`."""

import asyncio
import logging
from collections.abc import Callable, Mapping, MutableMapping
from types import MappingProxyType
from typing import Any

import httpx

from nightloom_sdk.http import NetworkNotDeclaredError, create_http_client
from nightloom_sdk.meta import ShuttleMeta

ProgressCallback = Callable[[str], None]
PROGRESS_MAX_LENGTH = 200


class JobCancelledError(Exception):
    """Raised by :meth:`ShuttleContext.raise_if_cancelled` once the job was cancelled."""


class _PrefixedLogger(logging.LoggerAdapter[logging.Logger]):
    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        return f"[{self.extra['shuttle'] if self.extra else '?'}] {msg}", kwargs


class ShuttleContext:
    """Services for one Shuttle run.

    - ``logger``: a logger whose messages start with ``[<shuttle name>]``.
      Never log secrets or whole responses.
    - ``http``: the guarded :class:`httpx.AsyncClient` (see
      :mod:`nightloom_sdk.http`). Only for Shuttles that declare
      ``network=True``; others get :class:`NetworkNotDeclaredError`.
    - ``keys``: the API keys the Shuttle declared in ``required_keys``,
      read-only.
    - ``cancelled`` / :meth:`raise_if_cancelled`: whether the job was
      cancelled. The runner also stops between findings, so long loops only
      need to check before expensive work.
    - :meth:`progress`: a short, human-readable status line.
    """

    def __init__(
        self,
        meta: ShuttleMeta,
        *,
        keys: Mapping[str, str] | None = None,
        http: httpx.AsyncClient | None = None,
        cancel_event: asyncio.Event | None = None,
        on_progress: ProgressCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.meta = meta
        self.logger: logging.LoggerAdapter[logging.Logger] = _PrefixedLogger(
            logger or logging.getLogger(f"nightloom.shuttle.{meta.name}"), {"shuttle": meta.name}
        )
        self.keys: Mapping[str, str] = MappingProxyType(dict(keys or {}))
        self._http = http
        self._cancel_event = cancel_event or asyncio.Event()
        self._on_progress = on_progress
        self.last_progress: str | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if not self.meta.network:
            raise NetworkNotDeclaredError(
                f"Shuttle {self.meta.name} declares network=False and cannot make requests."
            )
        if self._http is None:
            self._http = create_http_client(self.meta)
        return self._http

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelledError("The job was cancelled.")

    def progress(self, message: str) -> None:
        text = " ".join(str(message).split())[:PROGRESS_MAX_LENGTH]
        self.last_progress = text
        self.logger.info("progress: %s", text)
        if self._on_progress is not None:
            self._on_progress(text)

    async def aclose(self) -> None:
        """Close the HTTP client (the context owns it, whether given or created)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None


__all__ = ["JobCancelledError", "ShuttleContext"]
