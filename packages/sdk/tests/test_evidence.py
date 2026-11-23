"""Evidence a Shuttle keeps with the case: names, types, limits."""

import hashlib

import pytest

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EvidenceError, EvidenceUnavailableError, ShuttleContext, ShuttleMeta
from nightloom_sdk.evidence import (
    EVIDENCE_NAME,
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_PER_RUN,
    MemoryEvidenceStore,
    evidence_name,
)
from nightloom_sdk.testing import fake_context

META = ShuttleMeta.model_validate(
    {
        "name": "pictures",
        "version": "1.0.0",
        "title": "Pictures",
        "description": "Keeps pictures.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.URL},
        "output_relationship_types": {RelationType.HOSTS},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
    }
)


def test_names_are_the_sha256_and_the_extension() -> None:
    data = b"\x89PNG fake"
    name = evidence_name(data, "image/png")
    assert name == f"{hashlib.sha256(data).hexdigest()}.png"
    assert EVIDENCE_NAME.fullmatch(name)
    assert evidence_name(data, "image/jpeg").endswith(".jpg")
    with pytest.raises(EvidenceError):
        evidence_name(data, "text/html")


async def test_saved_once_per_name() -> None:
    store = MemoryEvidenceStore()
    ctx = fake_context(META, evidence=store)
    first = await ctx.save_evidence(b"one", content_type="image/png")
    again = await ctx.save_evidence(b"one", content_type="image/png")
    assert first == again
    assert store.files == {first: (b"one", "image/png")}


async def test_limits() -> None:
    ctx = fake_context(META)
    with pytest.raises(EvidenceError):
        await ctx.save_evidence(b"", content_type="image/png")
    with pytest.raises(EvidenceError):
        await ctx.save_evidence(b"x" * (MAX_EVIDENCE_BYTES + 1), content_type="image/png")
    with pytest.raises(EvidenceError):
        await ctx.save_evidence(b"<svg/>", content_type="image/svg+xml")
    for n in range(MAX_EVIDENCE_PER_RUN):
        await ctx.save_evidence(bytes([n]), content_type="image/png")
    with pytest.raises(EvidenceError):
        await ctx.save_evidence(b"one too many", content_type="image/png")
    # A file already saved in this run is not counted again.
    await ctx.save_evidence(bytes([0]), content_type="image/png")


async def test_no_store_means_unavailable() -> None:
    ctx = ShuttleContext(META)
    with pytest.raises(EvidenceUnavailableError):
        await ctx.save_evidence(b"one", content_type="image/png")
