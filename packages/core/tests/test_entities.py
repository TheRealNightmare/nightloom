"""Entity models: normalization on construction, keys, sensitivity and properties."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from nightloom_core.entities import (
    ENTITY_MODELS,
    AnyEntity,
    Breach,
    Domain,
    EntityType,
    Phone,
    Sensitivity,
    build_entity,
    label_of,
    sensitivity_of,
)
from nightloom_core.normalize import NormalizationError, normalize

PERSON_TYPES = {
    EntityType.PERSON,
    EntityType.EMAIL,
    EntityType.USERNAME,
    EntityType.PHONE,
    EntityType.SOCIAL_PROFILE,
    EntityType.LOCATION,
    EntityType.IMAGE,
}
ORGANIZATION_TYPES = {
    EntityType.ORGANIZATION,
    EntityType.CRYPTO_WALLET,
    EntityType.DOCUMENT,
    EntityType.BREACH,
}


def test_one_model_per_type() -> None:
    assert set(ENTITY_MODELS) == set(EntityType)
    for entity_type, model in ENTITY_MODELS.items():
        assert model.entity_type == entity_type
        assert model.model_fields["type"].default == entity_type
    labels = [label_of(entity_type) for entity_type in EntityType]
    assert len(set(labels)) == len(labels)
    assert all(label.isidentifier() and label[0].isupper() for label in labels)
    assert "Entity" not in labels


@pytest.mark.parametrize("entity_type", list(EntityType))
def test_display_metadata(entity_type: EntityType) -> None:
    model = ENTITY_MODELS[entity_type]
    assert model.display_name.strip() == model.display_name != ""
    assert model.hint.endswith(".")
    # The example is a valid value of its type, and documentation-safe.
    entity = build_entity(entity_type, model.example)
    assert entity.key == normalize(entity_type, model.example).key
    assert "@" not in model.example or "example.org" in model.example


def test_display_names_are_unique() -> None:
    names = [model.display_name for model in ENTITY_MODELS.values()]
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("entity_type", list(EntityType))
def test_sensitivity(entity_type: EntityType) -> None:
    if entity_type in PERSON_TYPES:
        expected = Sensitivity.PERSON
    elif entity_type in ORGANIZATION_TYPES:
        expected = Sensitivity.ORGANIZATION
    else:
        expected = Sensitivity.INFRASTRUCTURE
    assert sensitivity_of(entity_type) == expected


def test_value_is_normalized_on_construction() -> None:
    domain = Domain(value="Example.ORG.")
    assert domain.value == "example.org"
    assert domain.key == "domain:example.org"
    assert domain.type == EntityType.DOMAIN
    assert domain == Domain(value="example.org")


def test_invalid_value_is_a_validation_error_with_the_safe_message() -> None:
    with pytest.raises(ValidationError) as info:
        Domain(value="not a domain")
    assert "must not contain spaces" in str(info.value)
    with pytest.raises(ValidationError):
        Domain(value=42)  # type: ignore[arg-type]


def test_models_are_frozen_and_strict() -> None:
    domain = Domain(value="example.org")
    with pytest.raises(ValidationError):
        domain.value = "other.org"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Domain(value="example.org", extra="x")  # type: ignore[call-arg]


def test_discriminated_union() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(AnyEntity)
    entity = adapter.validate_python({"type": "ip_address", "value": "2001:DB8::1"})
    assert entity.key == "ip_address:2001:db8::1"
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "spaceship", "value": "x"})


def test_build_entity_with_properties() -> None:
    breach = build_entity(
        EntityType.BREACH,
        "  Example  Breach 2024 ",
        {"breach_date": "2024-03-01", "record_count": 10, "data_classes": [" emails "]},
    )
    assert isinstance(breach, Breach)
    assert breach.value == "Example Breach 2024"
    assert breach.key == "breach:example breach 2024"
    assert breach.stored_properties() == {
        "breach_date": "2024-03-01",
        "record_count": 10,
        "data_classes": ["emails"],
    }


def test_build_entity_without_properties() -> None:
    entity = build_entity(EntityType.DOMAIN, "example.org")
    assert entity.properties is None
    assert entity.stored_properties() == {}
    assert build_entity(EntityType.DOMAIN, "example.org", {}).stored_properties() == {}


@pytest.mark.parametrize(
    ("entity_type", "properties"),
    [
        (EntityType.DOMAIN, {"anything": 1}),
        (EntityType.NETBLOCK, {"country": "Bangladesh"}),
        (EntityType.URL, {"status_code": 99}),
        (EntityType.LOCATION, {"latitude": 91}),
        (EntityType.LOCATION, {"longitude": float("nan")}),
        (EntityType.DOCUMENT, {"sha256": "abc"}),
        (EntityType.IMAGE, {"mime_type": "not a type"}),
        (EntityType.CERTIFICATE, {"not_after": "2026-01-01T00:00:00"}),  # naive
        (EntityType.CERTIFICATE, {"crtsh_id": 0}),
        (EntityType.CERTIFICATE, {"san_count": -1}),
        (EntityType.CERTIFICATE, {"subject_cn": "x" * 201}),
        (EntityType.BREACH, {"data_classes": ["x"] * 51}),
        (EntityType.DOMAIN, {"dns_txt": ["x"] * 51}),  # at most 50 records
        (EntityType.DOMAIN, {"dns_txt": ["x" * 1001]}),  # of at most 1,000 characters
        (EntityType.DOMAIN, {"dns_txt": [""]}),
        (EntityType.DOMAIN, {"dns_soa_serial": 2**32}),
        (EntityType.DOMAIN, {"dns_soa_retry": -1}),
        (EntityType.IP_ADDRESS, {"scope": "internal"}),
    ],
)
def test_invalid_properties(entity_type: EntityType, properties: dict[str, Any]) -> None:
    value = {
        EntityType.DOMAIN: "example.org",
        EntityType.NETBLOCK: "192.0.2.0/24",
        EntityType.URL: "https://example.org/",
        EntityType.CERTIFICATE: "a" * 64,
        EntityType.IP_ADDRESS: "192.0.2.1",
    }.get(entity_type, "Something")
    with pytest.raises(ValidationError):
        build_entity(entity_type, value, properties)


def test_properties_are_typed_and_serialized_for_storage() -> None:
    cert = build_entity(
        EntityType.CERTIFICATE,
        "A" * 64,
        {"subject": "CN=example.org", "not_after": "2027-01-01T00:00:00Z", "self_signed": False},
    )
    assert cert.stored_properties() == {
        "subject": "CN=example.org",
        "not_after": "2027-01-01T00:00:00Z",
        "self_signed": False,
    }
    crtsh = build_entity(
        EntityType.CERTIFICATE,
        "b" * 64,
        {"subject_cn": " *.example.org ", "crtsh_id": 123, "san_count": 2},
    )
    assert crtsh.stored_properties() == {
        "subject_cn": "*.example.org",
        "crtsh_id": 123,
        "san_count": 2,
    }
    netblock = build_entity(EntityType.NETBLOCK, "192.0.2.0/24", {"country": " bd "})
    assert netblock.stored_properties() == {"country": "BD"}


def test_dns_properties_of_domains_and_ip_addresses() -> None:
    domain = build_entity(
        EntityType.DOMAIN,
        "example.org",
        {
            "dns_txt": ["v=spf1 -all", " google-site-verification=abc "],
            "dns_spf": ["v=spf1 -all"],
            "dns_dmarc": ["v=DMARC1; p=reject"],
            "dns_mx": ["10 mail.example.org"],
            "dns_caa": ['0 issue "letsencrypt.org"'],
            "dns_soa_primary_ns": "ns1.example.org",
            "dns_soa_responsible": "hostmaster.example.org",
            "dns_soa_serial": 2**32 - 1,
            "dns_soa_refresh": 7200,
            "dns_soa_retry": 3600,
            "dns_soa_expire": 1209600,
            "dns_soa_minimum": 3600,
        },
    )
    stored = domain.stored_properties()
    assert stored["dns_txt"] == ["v=spf1 -all", "google-site-verification=abc"]
    assert stored["dns_soa_serial"] == 4294967295
    assert len(stored) == 12
    full = build_entity(EntityType.DOMAIN, "example.org", {"dns_txt": ["x" * 1000] * 50})
    assert len(full.stored_properties()["dns_txt"]) == 50
    ip = build_entity(EntityType.IP_ADDRESS, "10.0.0.1", {"scope": "private"})
    assert ip.stored_properties() == {"scope": "private"}
    assert build_entity(EntityType.IP_ADDRESS, "192.0.2.1", {"scope": "public"}).stored_properties()


def test_build_entity_phone_region() -> None:
    phone = build_entity(EntityType.PHONE, "020 7946 0958", phone_region="GB")
    assert isinstance(phone, Phone)
    assert phone.value == "+442079460958"
    with pytest.raises(NormalizationError, match="international format"):
        build_entity(EntityType.PHONE, "020 7946 0958")
    # The region is ignored for every other type.
    assert build_entity(EntityType.DOMAIN, "example.org", phone_region="GB").key == (
        "domain:example.org"
    )


def test_build_entity_raises_normalization_error() -> None:
    with pytest.raises(NormalizationError, match="Not a valid IP address"):
        build_entity(EntityType.IP_ADDRESS, "999.1.1.1")
