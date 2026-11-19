"""Export formats, redaction and file names, without any service."""

import hashlib
import json
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any

import pytest

from export_checks import graphml_items, read_csv, read_json, read_zip, validate_graphml
from nightloom_api.export import formats
from nightloom_api.export.formats import (
    ExportFormat,
    ExportHeader,
    csv_cell,
    csv_zip_chunks,
    graphml_chunks,
    json_chunks,
)
from nightloom_api.export.redaction import (
    REDACTED_KEY_PREFIX,
    REDACTED_VALUE,
    ExportEntity,
    ExportRelationship,
    Redactor,
)
from nightloom_api.export.service import case_slug, export_filename
from nightloom_api.graph.repository import EntityRecord, Provenance, RelationshipRecord
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType

SEEN = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
SECRET = b"k" * 32
HEADER = ExportHeader(
    case_id="7c5e0f6e-2b1c-4b8e-9d0e-6a3f1c2b4d5e",
    title="Example <exposure> & review",
    legal_basis="owned_assets",
    exported_at=SEEN,
    exported_by_label="Test User",
    nightloom_version="0.1.0",
    include_person_data=False,
)
# Fictional person data that must never appear in a redacted export.
PERSON_VALUES = ("alice@example.net", "Alice Example", "alice example", "+442079460958")


def provenance(note: str | None = None, refs: Iterable[str] = ()) -> Provenance:
    return Provenance(
        first_seen=SEEN,
        last_seen=SEEN,
        observation_count=1,
        sources=["manual", "shuttle:dns@1.0.0"],
        max_confidence=0.8,
        admiralty="B2",
        evidence_refs=list(refs),
        note=note,
    )


def entity(
    type_: EntityType,
    key: str,
    value: str,
    sensitivity: Sensitivity = Sensitivity.INFRASTRUCTURE,
    **properties: Any,
) -> EntityRecord:
    return EntityRecord(
        key=key,
        type=type_,
        value=value,
        sensitivity=sensitivity,
        provenance=provenance(note=f"seen {value}", refs=[f"evidence of {value}"]),
        properties=properties,
    )


def relationship(
    type_: RelationType,
    source: EntityRecord,
    target: EntityRecord,
    note: str | None = None,
) -> RelationshipRecord:
    return RelationshipRecord(
        type=type_,
        source_key=source.key,
        target_key=target.key,
        provenance=provenance(note=note),
        source_sensitivity=source.sensitivity,
        target_sensitivity=target.sensitivity,
    )


DOMAIN = entity(EntityType.DOMAIN, "domain:example.org", "example.org", dns_txt=["v=spf1 -all"])
URL = entity(EntityType.URL, "url:https://example.org/a?b", "https://example.org/a?b")
EMAIL = entity(EntityType.EMAIL, "email:alice@example.net", "alice@example.net", Sensitivity.PERSON)
PERSON = entity(EntityType.PERSON, "person:alice example", "Alice Example", Sensitivity.PERSON)
ENTITIES = [DOMAIN, URL, EMAIL, PERSON]
RELATIONSHIPS = [
    relationship(RelationType.HOSTS, DOMAIN, URL, note="home page"),
    relationship(RelationType.HAS_EMAIL, DOMAIN, EMAIL, note="contact alice@example.net"),
    relationship(RelationType.ACCOUNT_OF, EMAIL, PERSON, note="Alice Example"),
]


async def aiter[T](items: Iterable[T]) -> AsyncIterator[T]:
    for item in items:
        yield item


def exported(redactor: Redactor) -> tuple[list[ExportEntity], list[ExportRelationship]]:
    return (
        [redactor.entity(record) for record in ENTITIES],
        [redactor.relationship(record) for record in RELATIONSHIPS],
    )


async def collect(chunks: AsyncIterable[bytes]) -> list[bytes]:
    return [chunk async for chunk in chunks]


def assert_no_person_data(data: bytes) -> None:
    text = data.decode()
    for value in PERSON_VALUES:
        assert value not in text


# --- redaction ----------------------------------------------------------------------


def test_redaction_hides_values_and_keeps_type_and_sensitivity() -> None:
    redactor = Redactor(include_person_data=False, secret=SECRET)
    email = redactor.entity(EMAIL)
    assert (email.type, email.sensitivity, email.value) == (
        EntityType.EMAIL,
        Sensitivity.PERSON,
        REDACTED_VALUE,
    )
    assert email.redacted
    assert email.key.startswith(REDACTED_KEY_PREFIX)
    assert "alice" not in email.key
    assert email.properties == {}
    assert email.provenance.note is None
    assert email.provenance.evidence_refs == []
    # Provenance that says nothing about the person stays.
    assert email.provenance.sources == EMAIL.provenance.sources

    domain = redactor.entity(DOMAIN)
    assert (domain.key, domain.value, domain.redacted) == (DOMAIN.key, DOMAIN.value, False)
    assert domain.properties == {"dns_txt": ["v=spf1 -all"]}
    assert domain.provenance.note == "seen example.org"


def test_redacted_keys_are_stable_within_an_export_and_differ_between_exports() -> None:
    one = Redactor(include_person_data=False, secret=SECRET)
    other = Redactor(include_person_data=False)
    assert one.key(EMAIL.key, Sensitivity.PERSON) == one.key(EMAIL.key, Sensitivity.PERSON)
    assert one.key(EMAIL.key, Sensitivity.PERSON) != one.key(PERSON.key, Sensitivity.PERSON)
    assert one.key(EMAIL.key, Sensitivity.PERSON) != other.key(EMAIL.key, Sensitivity.PERSON)
    # An unknown sensitivity is treated as personal.
    assert one.key(EMAIL.key, None).startswith(REDACTED_KEY_PREFIX)


def test_redacted_relationships_still_connect_and_lose_their_notes() -> None:
    redactor = Redactor(include_person_data=False, secret=SECRET)
    entities, relationships = exported(redactor)
    keys = {item.key for item in entities}
    assert all({rel.source_key, rel.target_key} <= keys for rel in relationships)
    hosts, has_email, account_of = relationships
    assert hosts.provenance.note == "home page"
    assert has_email.provenance.note is None
    assert account_of.provenance.note is None
    assert has_email.source_key == DOMAIN.key
    assert account_of.id == f"{account_of.source_key}|ACCOUNT_OF|{account_of.target_key}"


def test_include_person_data_exports_everything() -> None:
    redactor = Redactor(include_person_data=True)
    entities, relationships = exported(redactor)
    email = entities[2]
    assert (email.key, email.value, email.redacted) == (EMAIL.key, EMAIL.value, False)
    assert relationships[2].provenance.note == "Alice Example"


# --- file names ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Example exposure review", "example-exposure-review"),
        ("  Bücher & Co. — Q4!  ", "bucher-co-q4"),
        ("中文标题", "case"),
        ("a" * 80, "a" * 50),
        ("x" * 49 + " y", "x" * 49),
    ],
)
def test_case_slug(title: str, slug: str) -> None:
    assert case_slug(title) == slug


def test_export_filename_uses_utc() -> None:
    from datetime import timedelta, timezone

    local = datetime(2026, 11, 19, 14, 30, 5, tzinfo=timezone(timedelta(hours=2)))
    assert export_filename("Example review", ExportFormat.CSV, local) == (
        "nightloom-example-review-20261119T123005Z.zip"
    )
    assert export_filename("x", ExportFormat.GRAPHML, local).endswith(".graphml")
    assert export_filename("x", ExportFormat.JSON, local).endswith(".json")


# --- CSV injection ------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_cells_starting_with_a_formula_character_are_quoted(prefix: str) -> None:
    assert csv_cell(f"{prefix}SUM(A1)") == f"'{prefix}SUM(A1)"


def test_csv_cells_otherwise_unchanged() -> None:
    assert csv_cell("example.org") == "example.org"
    assert csv_cell("a=b") == "a=b"
    assert csv_cell(None) == ""
    assert csv_cell(True) == "true"
    assert csv_cell(3) == "3"
    assert csv_cell(0.8) == "0.8"


# --- formats ------------------------------------------------------------------------------


async def test_json_export() -> None:
    entities, relationships = exported(Redactor(include_person_data=False, secret=SECRET))
    data = b"".join(await collect(json_chunks(HEADER, aiter(entities), aiter(relationships))))
    assert_no_person_data(data)
    document = read_json(data)
    assert document["case"] == {
        "id": HEADER.case_id,
        "title": HEADER.title,
        "legal_basis": "owned_assets",
        "exported_at": "2026-11-01T12:00:00+00:00",
        "exported_by_label": "Test User",
        "nightloom_version": "0.1.0",
        "include_person_data": False,
    }
    assert [item["type"] for item in document["entities"]] == ["domain", "url", "email", "person"]
    domain = document["entities"][0]
    assert domain["provenance"] == {
        "first_seen": "2026-11-01T12:00:00+00:00",
        "last_seen": "2026-11-01T12:00:00+00:00",
        "observation_count": 1,
        "sources": ["manual", "shuttle:dns@1.0.0"],
        "max_confidence": 0.8,
        "admiralty": "B2",
        "evidence_refs": ["evidence of example.org"],
        "note": "seen example.org",
    }
    assert document["entities"][2]["value"] == REDACTED_VALUE
    keys = {item["key"] for item in document["entities"]}
    assert all({rel["source_key"], rel["target_key"]} <= keys for rel in document["relationships"])


async def test_json_export_of_an_empty_graph() -> None:
    data = b"".join(await collect(json_chunks(HEADER, aiter([]), aiter([]))))
    assert read_json(data)["entities"] == []
    assert read_json(data)["relationships"] == []


async def test_large_exports_are_streamed_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formats, "CHUNK_SIZE", 1024)
    redactor = Redactor(include_person_data=False, secret=SECRET)
    # Random-looking names, so the ZIP's compressor has to emit as it goes.
    names = [f"{hashlib.sha256(str(i).encode()).hexdigest()}.example.org" for i in range(3000)]
    many = [redactor.entity(entity(EntityType.DOMAIN, f"domain:{name}", name)) for name in names]
    for writer in (json_chunks, graphml_chunks, csv_zip_chunks):
        chunks = await collect(writer(HEADER, aiter(many), aiter([])))
        assert len(chunks) > 10, writer.__name__
        assert max(len(chunk) for chunk in chunks[:-1]) < 64 * 1024


async def test_graphml_export_is_valid_and_typed() -> None:
    entities, relationships = exported(Redactor(include_person_data=False, secret=SECRET))
    data = b"".join(await collect(graphml_chunks(HEADER, aiter(entities), aiter(relationships))))
    assert_no_person_data(data)
    root = validate_graphml(data)
    nodes, edges = graphml_items(root)
    assert [node["type"] for node in nodes] == ["domain", "url", "email", "person"]
    assert nodes[1]["key"] == "url:https://example.org/a?b"
    assert nodes[0]["confidence"] == "0.8"
    assert nodes[0]["admiralty"] == "B2"
    assert nodes[0]["sources"] == "manual; shuttle:dns@1.0.0"
    assert nodes[2]["value"] == REDACTED_VALUE
    assert [edge["type"] for edge in edges] == ["HOSTS", "HAS_EMAIL", "ACCOUNT_OF"]
    assert edges[2]["source_key"] == nodes[2]["key"]
    assert edges[2]["target_key"] == nodes[3]["key"]
    # The title's markup is escaped, not interpreted.
    assert b"Example &lt;exposure&gt; &amp; review" in data


async def test_graphml_skips_dangling_edges_duplicates_and_bad_characters() -> None:
    redactor = Redactor(include_person_data=True)
    odd = redactor.entity(entity(EntityType.ORGANIZATION, "organization:x", "Bad\x01name"))
    dangling = redactor.relationship(relationship(RelationType.HOSTS, DOMAIN, URL))
    data = b"".join(
        await collect(
            graphml_chunks(HEADER, aiter([odd, odd, redactor.entity(DOMAIN)]), aiter([dangling]))
        )
    )
    root = validate_graphml(data)
    nodes, edges = graphml_items(root)
    assert len(nodes) == 2
    assert edges == []
    assert nodes[0]["value"] == "Bad�name"


async def test_csv_export_zip() -> None:
    redactor = Redactor(include_person_data=False, secret=SECRET)
    formula = redactor.entity(
        entity(
            EntityType.ORGANIZATION,
            "organization:=sum(a1)",
            "=SUM(A1)",
            Sensitivity.ORGANIZATION,
        )
    )
    entities, relationships = exported(redactor)
    data = b"".join(
        await collect(csv_zip_chunks(HEADER, aiter([*entities, formula]), aiter(relationships)))
    )
    files = read_zip(data)
    assert set(files) == {"README.txt", "entities.csv", "relationships.csv"}
    for content in files.values():
        assert_no_person_data(content.encode())
    rows = read_csv(files["entities.csv"])
    assert list(rows[0]) == list(formats.ENTITY_COLUMNS)
    assert rows[0]["key"] == "domain:example.org"
    assert json.loads(rows[0]["properties"]) == {"dns_txt": ["v=spf1 -all"]}
    assert rows[0]["sources"] == "manual; shuttle:dns@1.0.0"
    assert rows[2]["value"] == REDACTED_VALUE
    assert rows[2]["redacted"] == "true"
    assert rows[4]["value"] == "'=SUM(A1)"
    assert rows[4]["key"] == "organization:=sum(a1)"
    relationship_rows = read_csv(files["relationships.csv"])
    assert list(relationship_rows[0]) == list(formats.RELATIONSHIP_COLUMNS)
    keys = {row["key"] for row in rows}
    assert all({row["source_key"], row["target_key"]} <= keys for row in relationship_rows)
    readme = files["README.txt"]
    assert "REDACTED" in readme
    assert "entities.csv" in readme
    assert "single quote" in readme


async def test_csv_readme_when_person_data_is_included() -> None:
    header = ExportHeader(**{**HEADER.__dict__, "include_person_data": True})
    files = read_zip(b"".join(await collect(csv_zip_chunks(header, aiter([]), aiter([])))))
    assert "INCLUDED" in files["README.txt"]
    assert read_csv(files["entities.csv"]) == []
