"""Evidence files kept with a case (screenshots), in the S3-compatible object storage.

Each file is stored at ``cases/<case id>/evidence/<name>``, where ``name`` is
its evidence name (``<sha256>.<ext>``, see :mod:`nightloom_sdk.evidence`). The
worker writes them for Shuttles (:class:`S3EvidenceStore`); the API reads them
for the case's members (:func:`read`) and deletes them with the case
(:func:`delete_case`).
"""

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from nightloom_api.infra.storage import Storage
from nightloom_sdk.evidence import EVIDENCE_NAME, EVIDENCE_TYPES, MAX_EVIDENCE_BYTES

if TYPE_CHECKING:
    # Stubs only (dev dependency); not installed in the runtime image.
    from types_aiobotocore_s3.type_defs import ObjectIdentifierTypeDef

logger = logging.getLogger(__name__)

# Keys deleted per request (the S3 maximum), and at most this many requests per case.
DELETE_BATCH = 1000
MAX_DELETE_ROUNDS = 1000
CONTENT_TYPES = {extension: content_type for content_type, extension in EVIDENCE_TYPES.items()}


def case_prefix(case_id: uuid.UUID) -> str:
    return f"cases/{case_id}/evidence/"


def object_key(case_id: uuid.UUID, name: str) -> str:
    """The object key of a case's evidence file; ``name`` must be an evidence name."""
    if not EVIDENCE_NAME.fullmatch(name):
        raise ValueError("Not an evidence name.")
    return f"{case_prefix(case_id)}{name}"


def content_type_of(name: str) -> str:
    return CONTENT_TYPES[name.rsplit(".", 1)[1]]


class S3EvidenceStore:
    """Stores one case's evidence (an :class:`~nightloom_sdk.evidence.EvidenceStore`)."""

    def __init__(self, storage: Storage, case_id: uuid.UUID) -> None:
        self._storage = storage
        self._case_id = case_id

    async def save(self, name: str, data: bytes, content_type: str) -> None:
        if len(data) > MAX_EVIDENCE_BYTES or EVIDENCE_TYPES.get(content_type) is None:
            raise ValueError("Refused evidence.")
        client = await self._storage.client()
        await client.put_object(
            Bucket=self._storage.bucket,
            Key=object_key(self._case_id, name),
            Body=data,
            ContentType=content_type,
        )


@dataclass(frozen=True)
class EvidenceFile:
    data: bytes
    content_type: str


async def read(storage: Storage, case_id: uuid.UUID, name: str) -> EvidenceFile | None:
    """The case's evidence file ``name``, or ``None`` if there is none."""
    client = await storage.client()
    try:
        response = await client.get_object(Bucket=storage.bucket, Key=object_key(case_id, name))
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    async with response["Body"] as body:
        data = await body.read()
    return EvidenceFile(data=data, content_type=content_type_of(name))


async def delete_case(storage: Storage, case_id: uuid.UUID) -> int:
    """Delete every evidence file of the case; returns how many.

    Lists the first batch, deletes it and lists again until nothing is left
    (no continuation tokens: the listing changes as files go).
    """
    client = await storage.client()
    deleted = 0
    for _ in range(MAX_DELETE_ROUNDS):
        listing = await client.list_objects_v2(
            Bucket=storage.bucket, Prefix=case_prefix(case_id), MaxKeys=DELETE_BATCH
        )
        keys: list[ObjectIdentifierTypeDef] = [
            {"Key": item["Key"]} for item in listing.get("Contents", []) if "Key" in item
        ]
        if not keys:
            return deleted
        result = await client.delete_objects(
            Bucket=storage.bucket, Delete={"Objects": keys, "Quiet": True}
        )
        errors = (result or {}).get("Errors") or []
        deleted += len(keys) - len(errors)
        if errors:
            raise RuntimeError(f"{len(errors)} evidence file(s) could not be deleted.")
    raise RuntimeError("Too many evidence files to delete in one go.")


__all__ = [
    "EvidenceFile",
    "S3EvidenceStore",
    "case_prefix",
    "content_type_of",
    "delete_case",
    "object_key",
    "read",
]
