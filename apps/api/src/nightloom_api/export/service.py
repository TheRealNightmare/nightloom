"""Streaming a case's graph out in one of the export formats.

Everything is read in one Neo4j read transaction and written as it arrives,
so memory stays flat whatever the case's size, with one exception that keeps
the redaction honest: when person data is redacted, the person-related
entities (and the relationships touching them) are collected and sorted by
their pseudonymous key before they are written. Streamed in key order they
would sit in the alphabetical order of their real keys, which says something
about those keys.
"""

import logging
import re
import unicodedata
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from neo4j.exceptions import DriverError, Neo4jError

from nightloom_api.export.formats import EXTENSIONS, WRITERS, ExportFormat, ExportHeader
from nightloom_api.export.redaction import ExportEntity, ExportRelationship, Redactor
from nightloom_api.graph.repository import GraphOperations, GraphRepository

logger = logging.getLogger(__name__)

# Larger cases are refused (413): split the work or ask an operator.
MAX_EXPORT_ENTITIES = 100_000
# Neo4j's limit for the export's read transaction (seconds).
EXPORT_TRANSACTION_TIMEOUT = 300.0
SLUG_MAX_LENGTH = 50
REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500


def case_slug(title: str) -> str:
    """``Example exposure review`` → ``example-exposure-review`` (ASCII, at most 50 characters)."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].rstrip("-") or "case"


def export_filename(title: str, export_format: ExportFormat, now: datetime) -> str:
    """``nightloom-<case-slug>-<UTC timestamp>.<ext>``, e.g. ``…-20261119T120000Z.json``."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"nightloom-{case_slug(title)}-{stamp}.{EXTENSIONS[export_format]}"


async def _entities(
    ops: GraphOperations, case_id: uuid.UUID, redactor: Redactor
) -> AsyncIterator[ExportEntity]:
    if redactor.include_person_data:
        async for record in ops.iter_entities(case_id):
            yield redactor.entity(record)
        return
    async for record in ops.iter_entities(case_id, person=False):
        yield redactor.entity(record)
    redacted = [redactor.entity(record) async for record in ops.iter_entities(case_id, person=True)]
    redacted.sort(key=lambda entity: entity.key)
    for entity in redacted:
        yield entity


async def _relationships(
    ops: GraphOperations, case_id: uuid.UUID, redactor: Redactor
) -> AsyncIterator[ExportRelationship]:
    if redactor.include_person_data:
        async for record in ops.iter_relationships(case_id):
            yield redactor.relationship(record)
        return
    async for record in ops.iter_relationships(case_id, person=False):
        yield redactor.relationship(record)
    touching = [
        redactor.relationship(record)
        async for record in ops.iter_relationships(case_id, person=True)
    ]
    touching.sort(key=lambda relationship: relationship.id)
    for relationship in touching:
        yield relationship


async def stream_export(
    graph: GraphRepository,
    case_id: uuid.UUID,
    export_format: ExportFormat,
    header: ExportHeader,
    redactor: Redactor,
) -> AsyncIterator[bytes]:
    """The export's bytes. A Neo4j failure part-way is logged and ends the stream early."""
    try:
        async with graph.read(tx_timeout=EXPORT_TRANSACTION_TIMEOUT) as ops:
            async for chunk in WRITERS[export_format](
                header,
                _entities(ops, case_id, redactor),
                _relationships(ops, case_id, redactor),
            ):
                yield chunk
    except (Neo4jError, DriverError) as error:
        # Only the type: driver messages can quote parameter values.
        logger.error("Export of a case graph failed: %s", type(error).__name__)
        raise
