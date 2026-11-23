"""Evidence: files a Shuttle keeps with its findings, such as a page's screenshot.

A Shuttle calls :meth:`~nightloom_sdk.context.ShuttleContext.save_evidence`
with the bytes and their type; the runner stores them with the case (in its
object storage) and returns an **evidence name**, ``<sha256>.<ext>``, which
the Shuttle puts in a finding's properties. The name is the file's SHA-256,
so the same file is stored once per case, and nothing else about the file
(its address, the page it came from) is in it.

Only a few image types are accepted, at most :data:`MAX_EVIDENCE_BYTES` each
and :data:`MAX_EVIDENCE_PER_RUN` per run. The case's members read evidence
through the API, which checks their membership; deleting the case deletes it.
"""

import hashlib
import re
from typing import Protocol

MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE_PER_RUN = 5
# Accepted types and the extension of their evidence names.
EVIDENCE_TYPES: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg"}
EVIDENCE_NAME = re.compile(r"[0-9a-f]{64}\.(png|jpg)")


class EvidenceError(Exception):
    """The evidence was refused (type, size, count) or could not be stored."""


class EvidenceUnavailableError(EvidenceError):
    """This run has nowhere to store evidence."""


class EvidenceStore(Protocol):
    """Where a run's evidence goes (the runner's object storage, or memory in tests)."""

    async def save(self, name: str, data: bytes, content_type: str) -> None:
        """Store ``data`` under ``name`` (idempotent: the same name is the same file)."""


def evidence_name(data: bytes, content_type: str) -> str:
    """The evidence name of ``data``: its SHA-256 and the type's extension."""
    extension = EVIDENCE_TYPES.get(content_type)
    if extension is None:
        raise EvidenceError(f"Evidence of type {content_type!r} is not accepted.")
    return f"{hashlib.sha256(data).hexdigest()}.{extension}"


class MemoryEvidenceStore:
    """Keeps evidence in a dict (for tests)."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    async def save(self, name: str, data: bytes, content_type: str) -> None:
        self.files[name] = (data, content_type)


__all__ = [
    "EVIDENCE_NAME",
    "EVIDENCE_TYPES",
    "MAX_EVIDENCE_BYTES",
    "MAX_EVIDENCE_PER_RUN",
    "EvidenceError",
    "EvidenceStore",
    "EvidenceUnavailableError",
    "MemoryEvidenceStore",
    "evidence_name",
]
