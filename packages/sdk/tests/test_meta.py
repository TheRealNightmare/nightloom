"""ShuttleMeta validation."""

from typing import Any

import pytest
from pydantic import ValidationError

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk.meta import USAGE_NOTE_MAX_LENGTH, Mode, RateLimit, ShuttleMeta


def meta(**overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": "example-shuttle",
        "version": "1.2.3",
        "title": "Example",
        "description": "An example Shuttle for the tests.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


def test_valid_meta_and_defaults() -> None:
    m = meta()
    assert m.mode == Mode.PASSIVE
    assert m.timeout_seconds == 60
    assert m.required_keys == frozenset()
    assert m.source == "shuttle:example-shuttle@1.2.3"
    assert (m.default_admiralty, m.reliability, m.credibility) == ("F6", "F", 6)
    assert m.accepts(EntityType.DOMAIN)
    assert not m.accepts(EntityType.IP_ADDRESS)


def test_meta_is_frozen() -> None:
    m = meta()
    with pytest.raises(ValidationError):
        m.name = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "name", ["Example", "example_shuttle", "-example", "example-", "ex--ample", "1example", ""]
)
def test_name_must_be_a_lowercase_slug(name: str) -> None:
    with pytest.raises(ValidationError):
        meta(name=name)


def test_name_length_limit() -> None:
    meta(name="a" * 63)
    with pytest.raises(ValidationError):
        meta(name="a" * 64)


@pytest.mark.parametrize("version", ["1.0.0", "0.1.0-alpha.1", "2.3.4+build.5", "10.20.30"])
def test_semver_versions(version: str) -> None:
    assert meta(version=version).version == version


@pytest.mark.parametrize("version", ["1.0", "01.0.0", "1.0.0.0", "v1.0.0", "latest", "1.0.0-"])
def test_non_semver_versions_are_refused(version: str) -> None:
    with pytest.raises(ValidationError):
        meta(version=version)


def test_needs_an_input_and_an_output() -> None:
    with pytest.raises(ValidationError):
        meta(input_types=set())
    with pytest.raises(ValidationError, match="at least one output"):
        meta(output_entity_types=set(), output_relationship_types=set())


def test_relationship_outputs_must_fit_the_allow_list() -> None:
    # RESOLVES_TO is Domain -> IPAddress, but the Shuttle never sees an IP address.
    with pytest.raises(ValidationError, match="RESOLVES_TO cannot connect"):
        meta(output_relationship_types={RelationType.RESOLVES_TO})
    ok = meta(
        output_entity_types={EntityType.IP_ADDRESS},
        output_relationship_types={RelationType.RESOLVES_TO},
    )
    assert RelationType.RESOLVES_TO in ok.output_relationship_types


def test_person_outputs_need_person_sensitivity() -> None:
    with pytest.raises(ValidationError, match="at least person"):
        meta(
            output_entity_types={EntityType.EMAIL},
            output_relationship_types={RelationType.HAS_EMAIL},
        )
    assert (
        meta(
            output_entity_types={EntityType.EMAIL},
            output_relationship_types={RelationType.HAS_EMAIL},
            sensitivity=Sensitivity.PERSON,
        ).sensitivity
        == Sensitivity.PERSON
    )


def test_person_inputs_need_person_sensitivity() -> None:
    with pytest.raises(ValidationError, match="at least person"):
        meta(
            input_types={EntityType.EMAIL},
            output_entity_types={EntityType.DOMAIN},
            output_relationship_types=set(),
        )


def test_organization_outputs_need_at_least_organization() -> None:
    with pytest.raises(ValidationError, match="at least organization"):
        meta(output_entity_types={EntityType.ORGANIZATION}, output_relationship_types=set())
    # Overstating the sensitivity is allowed.
    assert meta(sensitivity=Sensitivity.PERSON).sensitivity == Sensitivity.PERSON


def test_network_shuttles_need_a_rate_limit() -> None:
    with pytest.raises(ValidationError, match="rate_limit"):
        meta(network=True)
    m = meta(network=True, rate_limit={"requests": 10, "period_seconds": 60})
    assert m.rate_limit == RateLimit(requests=10, period_seconds=60)
    assert m.rate_limit.per_second == pytest.approx(10 / 60)


@pytest.mark.parametrize("seconds", [0, 601])
def test_timeout_bounds(seconds: int) -> None:
    with pytest.raises(ValidationError):
        meta(timeout_seconds=seconds)
    assert meta(timeout_seconds=600).timeout_seconds == 600


@pytest.mark.parametrize("rating", ["A1", "F6", "C3"])
def test_admiralty_ratings(rating: str) -> None:
    assert meta(default_admiralty=rating).default_admiralty == rating


@pytest.mark.parametrize("rating", ["G1", "A7", "a1", "A", "A10"])
def test_bad_admiralty_ratings(rating: str) -> None:
    with pytest.raises(ValidationError):
        meta(default_admiralty=rating)


def test_urls_must_be_http() -> None:
    m = meta(homepage="https://example.org/", terms_url="http://example.org/terms")
    assert m.homepage == "https://example.org/"
    for bad in ("ftp://example.org/", "javascript:alert(1)", "not a url"):
        with pytest.raises(ValidationError):
            meta(homepage=bad)


def test_usage_note() -> None:
    assert meta().usage_note is None
    note = "Free for non-commercial use. Commercial products need a licence."
    assert meta(usage_note=f"  {note} ").usage_note == note  # trimmed
    assert meta(usage_note="x" * USAGE_NOTE_MAX_LENGTH).usage_note == "x" * 300
    for bad in (
        "x" * (USAGE_NOTE_MAX_LENGTH + 1),
        "",
        "   ",
        "two\nlines",
        "a\ttab",
        "bell\x07",
        "<b>bold</b>",
        "see <https://example.org>",
    ):
        with pytest.raises(ValidationError):
            meta(usage_note=bad)


def test_key_names_and_unknown_fields() -> None:
    assert meta(required_keys={"api_key"}).required_keys == {"api_key"}
    with pytest.raises(ValidationError):
        meta(required_keys={"API-KEY"})
    with pytest.raises(ValidationError):
        meta(risk_level="low")


def test_active_mode_is_declarable() -> None:
    # Declarable; the API and the worker refuse to run it.
    assert meta(mode="active").mode == Mode.ACTIVE
