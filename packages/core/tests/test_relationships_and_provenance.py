"""The relationship allow-list and provenance observations."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nightloom_core.entities import EntityType
from nightloom_core.provenance import (
    CREDIBILITY_SCALE,
    MANUAL_CONFIDENCE,
    MANUAL_CREDIBILITY,
    MANUAL_RELIABILITY,
    RELIABILITY_SCALE,
    Observation,
    manual_observation,
)
from nightloom_core.relationships import (
    ALLOWED,
    NOTE_REQUIRED,
    RelationshipError,
    RelationType,
    is_allowed,
    validate_relationship,
)

E = EntityType
R = RelationType
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

ALLOWED_CASES = [
    (R.HAS_SUBDOMAIN, E.DOMAIN, E.DOMAIN),
    (R.RESOLVES_TO, E.DOMAIN, E.IP_ADDRESS),
    (R.HAS_MX, E.DOMAIN, E.DOMAIN),
    (R.HAS_NS, E.DOMAIN, E.DOMAIN),
    (R.ALIAS_OF, E.DOMAIN, E.DOMAIN),
    (R.HAS_PTR, E.IP_ADDRESS, E.DOMAIN),
    (R.WITHIN, E.IP_ADDRESS, E.NETBLOCK),
    (R.WITHIN, E.NETBLOCK, E.NETBLOCK),
    (R.ANNOUNCED_BY, E.NETBLOCK, E.ASN),
    (R.COVERS, E.CERTIFICATE, E.DOMAIN),
    (R.PRESENTS, E.SERVICE, E.CERTIFICATE),
    (R.RUNS_ON, E.SERVICE, E.IP_ADDRESS),
    (R.USES, E.DOMAIN, E.TECHNOLOGY),
    (R.USES, E.URL, E.TECHNOLOGY),
    (R.USES, E.SERVICE, E.TECHNOLOGY),
    (R.OWNS, E.ORGANIZATION, E.DOMAIN),
    (R.OWNS, E.ORGANIZATION, E.NETBLOCK),
    (R.OWNS, E.ORGANIZATION, E.ASN),
    (R.HOSTS, E.DOMAIN, E.URL),
    (R.HAS_FAVICON, E.DOMAIN, E.FAVICON),
    (R.LOOKALIKE_OF, E.DOMAIN, E.DOMAIN),
    (R.HAS_FAVICON, E.URL, E.FAVICON),
    (R.REGISTERED_BY, E.DOMAIN, E.ORGANIZATION),
    (R.HAS_EMAIL, E.DOMAIN, E.EMAIL),
    (R.ACCOUNT_OF, E.USERNAME, E.PERSON),
    (R.ACCOUNT_OF, E.SOCIAL_PROFILE, E.PERSON),
    (R.ACCOUNT_OF, E.EMAIL, E.PERSON),
    (R.MENTIONS, E.DOCUMENT, E.PERSON),
    (R.MENTIONS, E.DOCUMENT, E.DOMAIN),
]

REFUSED_CASES = [
    (R.RESOLVES_TO, E.IP_ADDRESS, E.DOMAIN),  # wrong direction
    (R.RESOLVES_TO, E.PERSON, E.IP_ADDRESS),
    (R.HAS_SUBDOMAIN, E.DOMAIN, E.URL),
    (R.WITHIN, E.DOMAIN, E.NETBLOCK),
    (R.WITHIN, E.NETBLOCK, E.IP_ADDRESS),  # wrong direction
    (R.OWNS, E.PERSON, E.DOMAIN),
    (R.OWNS, E.ORGANIZATION, E.PERSON),
    (R.ACCOUNT_OF, E.PHONE, E.PERSON),
    (R.ACCOUNT_OF, E.PERSON, E.USERNAME),
    (R.MENTIONS, E.DOMAIN, E.DOCUMENT),
    (R.HAS_EMAIL, E.ORGANIZATION, E.EMAIL),
    (R.ALIAS_OF, E.DOMAIN, E.IP_ADDRESS),  # a CNAME points to a name, never an address
    (R.ALIAS_OF, E.URL, E.DOMAIN),
    (R.HAS_PTR, E.DOMAIN, E.IP_ADDRESS),  # wrong direction: that is RESOLVES_TO
    (R.HAS_PTR, E.NETBLOCK, E.DOMAIN),
    (R.HAS_PTR, E.IP_ADDRESS, E.URL),
]


def test_every_relation_type_has_an_entry() -> None:
    assert set(ALLOWED) == set(RelationType)
    assert {R.RELATED_TO} == NOTE_REQUIRED


@pytest.mark.parametrize(("relation", "source", "target"), ALLOWED_CASES)
def test_allowed(relation: RelationType, source: EntityType, target: EntityType) -> None:
    assert is_allowed(relation, source, target)
    validate_relationship(relation, source, target)


@pytest.mark.parametrize(("relation", "source", "target"), REFUSED_CASES)
def test_refused(relation: RelationType, source: EntityType, target: EntityType) -> None:
    assert not is_allowed(relation, source, target)
    with pytest.raises(RelationshipError, match=f"{relation.value} cannot connect a"):
        validate_relationship(relation, source, target, note="a note")


def test_allow_list_sizes() -> None:
    """Specific types stay narrow; only MENTIONS and RELATED_TO are wide."""
    assert len(ALLOWED[R.MENTIONS]) == len(EntityType)
    assert len(ALLOWED[R.RELATED_TO]) == len(EntityType) ** 2
    narrow = set(RelationType) - {R.MENTIONS, R.RELATED_TO}
    assert all(len(ALLOWED[relation]) <= 3 for relation in narrow)


def test_alias_of_is_a_cname_between_two_domains() -> None:
    assert ALLOWED[R.ALIAS_OF] == {(E.DOMAIN, E.DOMAIN)}
    assert R.ALIAS_OF.value == "ALIAS_OF"
    assert R.ALIAS_OF not in NOTE_REQUIRED


def test_has_ptr_links_an_address_to_its_reverse_dns_name() -> None:
    assert ALLOWED[R.HAS_PTR] == {(E.IP_ADDRESS, E.DOMAIN)}
    assert R.HAS_PTR.value == "HAS_PTR"
    assert R.HAS_PTR not in NOTE_REQUIRED


def test_lookalike_of_links_two_domains() -> None:
    assert ALLOWED[R.LOOKALIKE_OF] == {(E.DOMAIN, E.DOMAIN)}
    assert R.LOOKALIKE_OF not in NOTE_REQUIRED


def test_has_favicon_links_a_site_to_its_icon() -> None:
    assert ALLOWED[R.HAS_FAVICON] == {(E.DOMAIN, E.FAVICON), (E.URL, E.FAVICON)}
    assert not is_allowed(R.HAS_FAVICON, E.FAVICON, E.DOMAIN)
    assert R.HAS_FAVICON not in NOTE_REQUIRED


@pytest.mark.parametrize("note", [None, "", "   "])
def test_related_to_needs_a_note(note: str | None) -> None:
    with pytest.raises(RelationshipError, match="needs a note"):
        validate_relationship(R.RELATED_TO, E.PERSON, E.ORGANIZATION, note=note)


def test_related_to_with_a_note_connects_anything() -> None:
    for source in EntityType:
        validate_relationship(R.RELATED_TO, source, E.DOMAIN, note="Seen together in a report.")


def test_plain_strings_are_accepted() -> None:
    assert is_allowed("RESOLVES_TO", "domain", "ip_address")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        is_allowed("NOPE", "domain", "ip_address")  # type: ignore[arg-type]


# --- provenance ------------------------------------------------------------------------


def test_manual_observation_defaults() -> None:
    observation = manual_observation(NOW, note="  typed in  ")
    assert observation.source == "manual"
    assert observation.observed_at == NOW
    assert observation.confidence == MANUAL_CONFIDENCE == 0.8
    assert observation.admiralty_reliability == MANUAL_RELIABILITY == "B"
    assert observation.admiralty_credibility == MANUAL_CREDIBILITY == 2
    assert observation.admiralty == "B2"
    assert observation.note == "typed in"
    assert observation.evidence_ref is None
    assert manual_observation(NOW, note="  ").note is None


def test_shuttle_observation() -> None:
    observation = Observation(
        source="shuttle:dns@1.0.0",
        observed_at=NOW,
        confidence=1,
        admiralty_reliability="A",
        admiralty_credibility=1,
        evidence_ref="s3://nightloom-evidence/abc",
    )
    assert observation.admiralty == "A1"


VALID = {
    "source": "manual",
    "observed_at": NOW,
    "confidence": 0.5,
    "admiralty_reliability": "C",
    "admiralty_credibility": 3,
}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "Manual"),
        ("source", "shuttle:dns"),
        ("source", "shuttle:DNS@1.0"),
        ("source", "someone"),
        ("observed_at", datetime(2026, 1, 1)),  # naive
        ("confidence", -0.1),
        ("confidence", 1.01),
        ("confidence", float("nan")),
        ("admiralty_reliability", "G"),
        ("admiralty_reliability", "b"),
        ("admiralty_credibility", 0),
        ("admiralty_credibility", 7),
        ("evidence_ref", ""),
        ("evidence_ref", "x" * 513),
        ("note", "x" * 501),
        ("unexpected", "x"),
    ],
)
def test_invalid_observation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate({**VALID, field: value})


def test_admiralty_scales_cover_every_grade() -> None:
    assert list(RELIABILITY_SCALE) == ["A", "B", "C", "D", "E", "F"]
    assert list(CREDIBILITY_SCALE) == [1, 2, 3, 4, 5, 6]
    assert MANUAL_RELIABILITY in RELIABILITY_SCALE
    assert MANUAL_CREDIBILITY in CREDIBILITY_SCALE
    for scale in (RELIABILITY_SCALE, CREDIBILITY_SCALE):
        assert all(text and text[0].isupper() for text in scale.values())
