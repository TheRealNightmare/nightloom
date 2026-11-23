"""What a running Shuttle can use: :class:`ShuttleContext`."""

import asyncio
import logging
from collections.abc import Callable, Mapping, MutableMapping
from types import MappingProxyType
from typing import Any

import httpx

from nightloom_sdk.evidence import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_PER_RUN,
    EvidenceError,
    EvidenceStore,
    EvidenceUnavailableError,
    evidence_name,
)
from nightloom_sdk.http import NetworkNotDeclaredError, create_http_client
from nightloom_sdk.meta import ShuttleMeta

ProgressCallback = Callable[[str], None]
PROGRESS_MAX_LENGTH = 200
# Job notes: at most this many per run, each at most this long.
MAX_NOTES = 10
NOTE_MAX_LENGTH = 200


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
    - :meth:`note`: a short remark kept with the job (shown to its users),
      e.g. that a result was narrowed or capped.
    - :meth:`save_evidence`: keep a file with the case (a screenshot), named
      by its SHA-256 (see :mod:`nightloom_sdk.evidence`).
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
        evidence: EvidenceStore | None = None,
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
        self._notes: list[str] = []
        self._evidence = evidence
        self._evidence_saved: set[str] = set()

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

    def note(self, message: str) -> None:
        """Keep a short remark with the job (at most MAX_NOTES; repeats are dropped).

        Notes are stored in the job's stats and shown to the case's members,
        but not copied into the audit log. Write fixed text and counts: never
        values the Shuttle found, responses or secrets.
        """
        text = " ".join(str(message).split())[:NOTE_MAX_LENGTH]
        if not text or text in self._notes:
            return
        self.logger.info("note: %s", text)
        if len(self._notes) < MAX_NOTES:
            self._notes.append(text)

    async def save_evidence(self, data: bytes, *, content_type: str) -> str:
        """Store ``data`` with the case; returns its evidence name (``<sha256>.<ext>``).

        Accepts PNG and JPEG images, at most MAX_EVIDENCE_BYTES each and
        MAX_EVIDENCE_PER_RUN different files per run. Raises
        :class:`EvidenceUnavailableError` when the run cannot store evidence,
        and :class:`EvidenceError` when the file is refused.
        """
        if self._evidence is None:
            raise EvidenceUnavailableError("This run cannot store evidence.")
        if not data or len(data) > MAX_EVIDENCE_BYTES:
            raise EvidenceError("Evidence must be between 1 byte and 5 MB.")
        name = evidence_name(data, content_type)
        if name not in self._evidence_saved and len(self._evidence_saved) >= MAX_EVIDENCE_PER_RUN:
            raise EvidenceError(f"A run may store at most {MAX_EVIDENCE_PER_RUN} files.")
        await self._evidence.save(name, data, content_type)
        self._evidence_saved.add(name)
        return name

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(self._notes)

    async def aclose(self) -> None:
        """Close the HTTP client (the context owns it, whether given or created)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None


__all__ = ["MAX_NOTES", "NOTE_MAX_LENGTH", "JobCancelledError", "ShuttleContext"]
