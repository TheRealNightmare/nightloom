"""The export formats, written as streams of bytes.

Each writer takes the export's header and two async iterables (entities, then
relationships) and yields chunks of roughly :data:`CHUNK_SIZE` bytes, so an
export of any size is sent without holding it in memory. The iterables yield
records that have already been through
:class:`~nightloom_api.export.redaction.Redactor`: the writers never see a
value that must not be exported.
"""

import csv
import io
import json
import re
import zipfile
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from nightloom_api.export.redaction import REDACTED_VALUE, ExportEntity, ExportRelationship
from nightloom_api.graph.repository import Provenance

CHUNK_SIZE = 64 * 1024
GRAPHML_NAMESPACE = "http://graphml.graphdrawing.org/xmlns"
GRAPHML_SCHEMA_LOCATION = "http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd"
LIST_SEPARATOR = "; "


class ExportFormat(StrEnum):
    JSON = "json"
    GRAPHML = "graphml"
    CSV = "csv"


# File extension and media type of each format's download.
EXTENSIONS = {ExportFormat.JSON: "json", ExportFormat.GRAPHML: "graphml", ExportFormat.CSV: "zip"}
MEDIA_TYPES = {
    ExportFormat.JSON: "application/json",
    ExportFormat.GRAPHML: "application/graphml+xml; charset=utf-8",
    ExportFormat.CSV: "application/zip",
}


@dataclass(frozen=True)
class ExportHeader:
    case_id: str
    title: str
    legal_basis: str
    exported_at: datetime
    exported_by_label: str
    nightloom_version: str
    include_person_data: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "title": self.title,
            "legal_basis": self.legal_basis,
            "exported_at": _timestamp(self.exported_at),
            "exported_by_label": self.exported_by_label,
            "nightloom_version": self.nightloom_version,
            "include_person_data": self.include_person_data,
        }


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _provenance(provenance: Provenance) -> dict[str, Any]:
    return {
        "first_seen": _timestamp(provenance.first_seen),
        "last_seen": _timestamp(provenance.last_seen),
        "observation_count": provenance.observation_count,
        "sources": list(provenance.sources),
        "max_confidence": provenance.max_confidence,
        "admiralty": provenance.admiralty,
        "evidence_refs": list(provenance.evidence_refs),
        "note": provenance.note,
    }


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return _timestamp(value)
    # Neo4j temporal values and anything else unusual become text.
    return str(value)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def entity_json(entity: ExportEntity) -> dict[str, Any]:
    return {
        "key": entity.key,
        "type": entity.type.value,
        "value": entity.value,
        "sensitivity": entity.sensitivity.value,
        "redacted": entity.redacted,
        "properties": entity.properties,
        "provenance": _provenance(entity.provenance),
    }


def relationship_json(relationship: ExportRelationship) -> dict[str, Any]:
    return {
        "id": relationship.id,
        "type": relationship.type.value,
        "source_key": relationship.source_key,
        "target_key": relationship.target_key,
        "provenance": _provenance(relationship.provenance),
    }


class _Chunks:
    """Collects text or bytes and hands them out in chunks of about CHUNK_SIZE."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._size = 0

    def add(self, data: str | bytes) -> None:
        encoded = data.encode() if isinstance(data, str) else data
        self._parts.append(encoded)
        self._size += len(encoded)

    @property
    def full(self) -> bool:
        return self._size >= CHUNK_SIZE

    def take(self) -> bytes:
        data = b"".join(self._parts)
        self._parts, self._size = [], 0
        return data


# --- JSON -------------------------------------------------------------------------------


async def json_chunks(
    header: ExportHeader,
    entities: AsyncIterable[ExportEntity],
    relationships: AsyncIterable[ExportRelationship],
) -> AsyncIterator[bytes]:
    """``{"case": {...}, "entities": [...], "relationships": [...]}``, one item at a time."""
    out = _Chunks()
    out.add('{"case":' + _dumps(header.as_dict()) + ',"entities":[')
    first = True
    async for entity in entities:
        out.add(("" if first else ",") + _dumps(entity_json(entity)))
        first = False
        if out.full:
            yield out.take()
    out.add('],"relationships":[')
    first = True
    async for relationship in relationships:
        out.add(("" if first else ",") + _dumps(relationship_json(relationship)))
        first = False
        if out.full:
            yield out.take()
    out.add("]}\n")
    yield out.take()


# --- GraphML ----------------------------------------------------------------------------

# Characters XML 1.0 does not allow, even escaped.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")

# (id, for, attr.name, attr.type)
GRAPH_KEYS = (
    ("g_case_id", "graph", "case_id", "string"),
    ("g_title", "graph", "title", "string"),
    ("g_legal_basis", "graph", "legal_basis", "string"),
    ("g_exported_at", "graph", "exported_at", "string"),
    ("g_nightloom_version", "graph", "nightloom_version", "string"),
    ("g_include_person_data", "graph", "include_person_data", "boolean"),
)
NODE_KEYS = (
    ("n_label", "node", "label", "string"),
    ("n_type", "node", "type", "string"),
    ("n_value", "node", "value", "string"),
    ("n_key", "node", "key", "string"),
    ("n_sensitivity", "node", "sensitivity", "string"),
    ("n_first_seen", "node", "first_seen", "string"),
    ("n_last_seen", "node", "last_seen", "string"),
    ("n_confidence", "node", "confidence", "double"),
    ("n_sources", "node", "sources", "string"),
    ("n_admiralty", "node", "admiralty", "string"),
)
EDGE_KEYS = (
    ("e_type", "edge", "type", "string"),
    ("e_first_seen", "edge", "first_seen", "string"),
    ("e_last_seen", "edge", "last_seen", "string"),
    ("e_confidence", "edge", "confidence", "double"),
    ("e_sources", "edge", "sources", "string"),
    ("e_admiralty", "edge", "admiralty", "string"),
)


def xml_text(value: object) -> str:
    """``value`` as escaped XML character data, without characters XML cannot carry."""
    return escape(_XML_INVALID.sub("�", str(value)))


def _data(key: str, value: object) -> str:
    if value is None:
        return ""
    return f'<data key="{key}">{xml_text(value)}</data>'


def _provenance_data(prefix: str, provenance: Provenance) -> str:
    return "".join(
        (
            _data(f"{prefix}_first_seen", _timestamp(provenance.first_seen)),
            _data(f"{prefix}_last_seen", _timestamp(provenance.last_seen)),
            _data(f"{prefix}_confidence", repr(float(provenance.max_confidence))),
            _data(f"{prefix}_sources", LIST_SEPARATOR.join(provenance.sources)),
            _data(f"{prefix}_admiralty", provenance.admiralty),
        )
    )


def _graphml_preamble(header: ExportHeader) -> str:
    keys = "".join(
        f'<key id="{key_id}" for="{scope}" attr.name="{name}" attr.type="{type_}"/>\n'
        for key_id, scope, name, type_ in (*GRAPH_KEYS, *NODE_KEYS, *EDGE_KEYS)
    )
    graph_data = "".join(
        (
            _data("g_case_id", header.case_id),
            _data("g_title", header.title),
            _data("g_legal_basis", header.legal_basis),
            _data("g_exported_at", _timestamp(header.exported_at)),
            _data("g_nightloom_version", header.nightloom_version),
            _data("g_include_person_data", "true" if header.include_person_data else "false"),
        )
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<graphml xmlns="{GRAPHML_NAMESPACE}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:schemaLocation="{GRAPHML_NAMESPACE} {GRAPHML_SCHEMA_LOCATION}">\n'
        f"{keys}"
        '<graph id="case" edgedefault="directed">\n'
        f"{graph_data}\n"
    )


async def graphml_chunks(
    header: ExportHeader,
    entities: AsyncIterable[ExportEntity],
    relationships: AsyncIterable[ExportRelationship],
) -> AsyncIterator[bytes]:
    """GraphML 1.0 with typed keys.

    Node IDs are ``n0``, ``n1``, … (an entity key is not a valid XML name
    token: URLs contain ``/`` and ``?``); the key is the ``key`` attribute.
    A relationship whose ends are not both in the file (the graph changed
    while it was being written) is left out, so the file stays valid.
    """
    out = _Chunks()
    out.add(_graphml_preamble(header))
    node_ids: dict[str, str] = {}
    async for entity in entities:
        if entity.key in node_ids:
            continue
        node_id = f"n{len(node_ids)}"
        node_ids[entity.key] = node_id
        out.add(
            f"<node id={quoteattr(node_id)}>"
            + _data("n_label", entity.value)
            + _data("n_type", entity.type.value)
            + _data("n_value", entity.value)
            + _data("n_key", entity.key)
            + _data("n_sensitivity", entity.sensitivity.value)
            + _provenance_data("n", entity.provenance)
            + "</node>\n"
        )
        if out.full:
            yield out.take()
    edge_count = 0
    async for relationship in relationships:
        source = node_ids.get(relationship.source_key)
        target = node_ids.get(relationship.target_key)
        if source is None or target is None:
            continue
        out.add(
            f"<edge id={quoteattr(f'e{edge_count}')} source={quoteattr(source)} "
            f"target={quoteattr(target)}>"
            + _data("e_type", relationship.type.value)
            + _provenance_data("e", relationship.provenance)
            + "</edge>\n"
        )
        edge_count += 1
        if out.full:
            yield out.take()
    out.add("</graph>\n</graphml>\n")
    yield out.take()


# --- CSV in a ZIP -----------------------------------------------------------------------

# Spreadsheet programs run a cell that starts with one of these as a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

ENTITY_COLUMNS = (
    "key",
    "type",
    "value",
    "sensitivity",
    "redacted",
    "first_seen",
    "last_seen",
    "observation_count",
    "sources",
    "max_confidence",
    "admiralty",
    "evidence_refs",
    "note",
    "properties",
)
RELATIONSHIP_COLUMNS = (
    "id",
    "type",
    "source_key",
    "target_key",
    "first_seen",
    "last_seen",
    "observation_count",
    "sources",
    "max_confidence",
    "admiralty",
    "evidence_refs",
    "note",
)


def csv_cell(value: object) -> str:
    """A CSV cell that no spreadsheet will evaluate (OWASP's CSV injection advice).

    Text starting with ``= + - @``, a tab or a carriage return gets a leading
    single quote, which spreadsheets show as text. Numbers are written by
    the export itself and never start with one of these.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _provenance_cells(provenance: Provenance) -> list[object]:
    return [
        _timestamp(provenance.first_seen),
        _timestamp(provenance.last_seen),
        provenance.observation_count,
        LIST_SEPARATOR.join(provenance.sources),
        float(provenance.max_confidence),
        provenance.admiralty,
        LIST_SEPARATOR.join(provenance.evidence_refs),
        provenance.note,
    ]


def entity_row(entity: ExportEntity) -> list[str]:
    return [
        csv_cell(cell)
        for cell in [
            entity.key,
            entity.type.value,
            entity.value,
            entity.sensitivity.value,
            entity.redacted,
            *_provenance_cells(entity.provenance),
            _dumps(entity.properties) if entity.properties else "",
        ]
    ]


def relationship_row(relationship: ExportRelationship) -> list[str]:
    return [
        csv_cell(cell)
        for cell in [
            relationship.id,
            relationship.type.value,
            relationship.source_key,
            relationship.target_key,
            *_provenance_cells(relationship.provenance),
        ]
    ]


def csv_readme(header: ExportHeader) -> str:
    person_data = (
        "INCLUDED. This export contains person-related data; the case owner gave a "
        "reason, which is recorded in the audit log. Handle it accordingly."
        if header.include_person_data
        else "REDACTED. Every person-related entity (person, email, username, phone, "
        "social profile, location, image) keeps its type and sensitivity, but its value "
        f'is "{REDACTED_VALUE}", its properties, note and evidence references are left '
        "out, and its key is a pseudonym (redacted:<HMAC>) that is the same everywhere in "
        "this export, so relationships still connect, and differs in every export. "
        "Relationships touching such an entity have no note or evidence references."
    )
    return f"""Nightloom case export
=====================

Case:          {header.title}
Case ID:       {header.case_id}
Legal basis:   {header.legal_basis}
Exported at:   {_timestamp(header.exported_at)}
Exported by:   {header.exported_by_label}
Nightloom:     {header.nightloom_version}
Person data:   {person_data}

Files
-----

entities.csv       one row per entity
relationships.csv  one row per relationship

Both are UTF-8, comma-separated, with a header row and RFC 4180 quoting.
Lists (sources, evidence_refs) are joined with "{LIST_SEPARATOR}". Timestamps are
ISO 8601 in UTC.

Cells that start with =, +, -, @, a tab or a carriage return are prefixed with a
single quote (') so that spreadsheet programs do not run them as formulas.
Remove that quote before using the value elsewhere.

entities.csv
------------

key                canonical key, <type>:<normalized value> (or redacted:<HMAC>)
type               entity type, e.g. domain, ip_address, email
value              the value as first seen (or "{REDACTED_VALUE}")
sensitivity        infrastructure, organization or person
redacted           true when the entity's data was redacted
first_seen         earliest observation
last_seen          latest observation
observation_count  how many observations were merged
sources            where it was observed: manual or shuttle:<name>@<version>
max_confidence     highest confidence seen, 0 to 1
admiralty          Admiralty rating of the latest observation: reliability A-F,
                   credibility 1-6 (e.g. B2)
evidence_refs      evidence references
note               the latest observation's note
properties         type-specific properties as a JSON object

relationships.csv
-----------------

id                 <source_key>|<type>|<target_key>
type               relationship type, e.g. RESOLVES_TO
source_key         key of the entity it starts from (see entities.csv)
target_key         key of the entity it points to
first_seen ... note   as in entities.csv
"""


class _Sink(io.RawIOBase):
    """A write-only, unseekable stream whose bytes are collected for the response."""

    def __init__(self) -> None:
        self.chunks = _Chunks()

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        payload = bytes(data)
        self.chunks.add(payload)
        return len(payload)


async def csv_zip_chunks(
    header: ExportHeader,
    entities: AsyncIterable[ExportEntity],
    relationships: AsyncIterable[ExportRelationship],
) -> AsyncIterator[bytes]:
    """A ZIP with README.txt, entities.csv and relationships.csv, streamed.

    The ZIP is written to an unseekable stream (sizes go into data
    descriptors), and ZIP64 is on for the CSV members, whose size is unknown
    up front.
    """
    sink = _Sink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", csv_readme(header))
        with (
            archive.open("entities.csv", mode="w", force_zip64=True) as raw,
            io.TextIOWrapper(raw, encoding="utf-8", newline="") as handle,
        ):
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(ENTITY_COLUMNS)
            async for entity in entities:
                writer.writerow(entity_row(entity))
                if sink.chunks.full:
                    yield sink.chunks.take()
        with (
            archive.open("relationships.csv", mode="w", force_zip64=True) as raw,
            io.TextIOWrapper(raw, encoding="utf-8", newline="") as handle,
        ):
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(RELATIONSHIP_COLUMNS)
            async for relationship in relationships:
                writer.writerow(relationship_row(relationship))
                if sink.chunks.full:
                    yield sink.chunks.take()
    yield sink.chunks.take()


WRITERS = {
    ExportFormat.JSON: json_chunks,
    ExportFormat.GRAPHML: graphml_chunks,
    ExportFormat.CSV: csv_zip_chunks,
}
