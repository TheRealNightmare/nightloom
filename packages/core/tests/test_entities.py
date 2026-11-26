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
    Favicon,
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
    EntityType.REPOSITORY,
    EntityType.SECRET_EXPOSURE,
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
    # An "@" means an address, which must be a documentation one; an exposure's "@" joins
    # its repository and commit instead.
    if entity_type is not EntityType.SECRET_EXPOSURE:
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


def test_a_favicon_is_named_by_its_hash() -> None:
    favicon = build_entity(
        EntityType.FAVICON,
        "3284398546",
        {"sha256": "A" * 64, "md5": "B" * 32, "mime_type": "image/x-icon", "size_bytes": 1150},
    )
    assert isinstance(favicon, Favicon)
    assert favicon.key == "favicon:-1010568750"
    assert favicon.stored_properties() == {
        "sha256": "a" * 64,
        "md5": "b" * 32,
        "mime_type": "image/x-icon",
        "size_bytes": 1150,
    }
    assert favicon.sensitivity == Sensitivity.INFRASTRUCTURE
    with pytest.raises(ValidationError):
        build_entity(EntityType.FAVICON, "1", {"md5": "z" * 32})


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


def test_exposure_properties_of_ip_addresses() -> None:
    ip = build_entity(
        EntityType.IP_ADDRESS,
        "192.0.2.10",
        {
            "open_ports": [22, 80, 443],
            "cpes": [" cpe:/a:nginx:nginx ", "cpe:/o:canonical:ubuntu_linux"],
            "vulns": ["CVE-2021-44228", "cve-2023-123456"],
            "tags": ["cdn", "self-signed"],
            "internetdb_checked_at": "2026-11-19T06:00:00Z",
        },
    )
    assert ip.stored_properties() == {
        "open_ports": [22, 80, 443],
        "cpes": ["cpe:/a:nginx:nginx", "cpe:/o:canonical:ubuntu_linux"],
        "vulns": ["CVE-2021-44228", "CVE-2023-123456"],
        "tags": ["cdn", "self-signed"],
        "internetdb_checked_at": "2026-11-19T06:00:00Z",
    }
    full = {
        "open_ports": list(range(1, 201)),
        "cpes": ["cpe:/a:x:y"] * 100,
        "vulns": ["CVE-2024-0001"] * 500,
        "tags": ["t"] * 50,
    }
    assert build_entity(EntityType.IP_ADDRESS, "192.0.2.10", full).stored_properties()


def test_lei_properties_of_organizations() -> None:
    org = build_entity(
        EntityType.ORGANIZATION,
        "Example Holdings",
        {
            "lei": " 5493001kjtiigc8y1r12 ",
            "legal_name": "EXAMPLE HOLDINGS LIMITED",
            "jurisdiction": "gb",
            "legal_form": "H0PO",
            "registered_as": "01234567",
            "entity_status": "active",
            "lei_status": "ISSUED",
            "country": "GB",
            "gleif_checked_at": "2026-11-24T06:00:00Z",
        },
    )
    assert org.stored_properties() == {
        "lei": "5493001KJTIIGC8Y1R12",
        "legal_name": "EXAMPLE HOLDINGS LIMITED",
        "jurisdiction": "GB",
        "legal_form": "H0PO",
        "registered_as": "01234567",
        "entity_status": "ACTIVE",
        "lei_status": "ISSUED",
        "country": "GB",
        "gleif_checked_at": "2026-11-24T06:00:00Z",
    }
    us_state = build_entity(EntityType.ORGANIZATION, "Example Inc.", {"jurisdiction": "US-DE"})
    assert us_state.stored_properties() == {"jurisdiction": "US-DE"}
    item = build_entity(
        EntityType.ORGANIZATION,
        "Example Inc.",
        {"wikidata_id": "q95", "wikidata_checked_at": "2026-11-24T06:00:00Z"},
    )
    assert item.stored_properties() == {
        "wikidata_id": "Q95",
        "wikidata_checked_at": "2026-11-24T06:00:00Z",
    }


@pytest.mark.parametrize(
    "properties",
    [
        {"lei": "5493001KJTIIGC8Y1R1"},  # 19 characters
        {"lei": "5493001KJTIIGC8Y1RAB"},  # check digits are digits
        {"jurisdiction": "Delaware"},
        {"jurisdiction": "US-"},
        {"entity_status": "active!"},
        {"lei_status": ""},
        {"gleif_checked_at": "2026-11-24T06:00:00"},  # naive
        {"wikidata_id": "Q0"},
        {"wikidata_id": "P31"},  # a property, not an item
        {"wikidata_id": "https://www.wikidata.org/wiki/Q95"},
    ],
)
def test_invalid_lei_properties(properties: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build_entity(EntityType.ORGANIZATION, "Example Holdings", properties)


def test_repository_and_github_properties() -> None:
    repository = build_entity(
        EntityType.REPOSITORY,
        "https://github.com/Example-Org/Widgets.git",
        {
            "description": "Widgets for everyone",
            "language": "Python",
            "topics": ["osint", "graph"],
            "license": "Apache-2.0",
            "stars": 12,
            "forks": 3,
            "fork": False,
            "archived": False,
            "homepage": "https://widgets.example.org/",
            "default_branch": "main",
            "pushed_at": "2026-11-20T08:00:00Z",
        },
    )
    assert repository.value == "github.com/example-org/widgets"
    assert repository.stored_properties()["topics"] == ["osint", "graph"]
    org = build_entity(EntityType.ORGANIZATION, "Example Org", {"github_login": " Example-Org "})
    assert org.stored_properties() == {"github_login": "example-org"}
    for bad in ("-example", "example--org", "a" * 40, "example_org"):
        with pytest.raises(ValidationError):
            build_entity(EntityType.ORGANIZATION, "Example Org", {"github_login": bad})
    with pytest.raises(ValidationError):
        build_entity(EntityType.REPOSITORY, "github.com/a/b", {"stars": -1})


def test_breach_properties() -> None:
    breach = build_entity(
        EntityType.BREACH,
        "ExampleShop",
        {
            "title": "Example Shop",
            "breach_date": "2024-03-01",
            "added_at": "2024-05-02T10:00:00Z",
            "record_count": 1200,
            "data_classes": ["Email addresses", "Passwords"],
            "verified": True,
            "fabricated": False,
            "spam_list": False,
            "malware": False,
        },
    )
    stored = breach.stored_properties()
    assert stored["title"] == "Example Shop"
    assert stored["verified"] is True and stored["fabricated"] is False
    assert stored["data_classes"] == ["Email addresses", "Passwords"]
    with pytest.raises(ValidationError):
        build_entity(EntityType.BREACH, "ExampleShop", {"added_at": "2024-05-02T10:00:00"})
    with pytest.raises(ValidationError):
        build_entity(EntityType.BREACH, "ExampleShop", {"record_count": -1})


def test_reverse_dns_property_of_ip_addresses() -> None:
    ip = build_entity(
        EntityType.IP_ADDRESS,
        "2001:db8::1",
        {"scope": "public", "ptr_checked_at": "2026-11-19T06:00:00+01:00"},
    )
    assert ip.stored_properties() == {
        "scope": "public",
        "ptr_checked_at": "2026-11-19T06:00:00+01:00",
    }


@pytest.mark.parametrize(
    "properties",
    [
        {"open_ports": [0]},
        {"open_ports": [65536]},
        {"open_ports": list(range(1, 202))},  # at most 200
        {"cpes": ["nginx"]},  # not a CPE name
        {"cpes": ["cpe:/a:x:y"] * 101},
        {"vulns": ["CVE-2021-123"]},  # at least four digits after the year
        {"vulns": ["GHSA-xxxx-yyyy-zzzz"]},
        {"vulns": ["CVE-2024-0001"] * 501},
        {"tags": [""]},
        {"tags": ["t"] * 51},
        {"internetdb_checked_at": "2026-11-19T06:00:00"},  # naive
        {"ptr_checked_at": "2026-11-19T06:00:00"},  # naive
        {"ptr_checked_at": "yesterday"},
    ],
)
def test_invalid_exposure_properties(properties: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build_entity(EntityType.IP_ADDRESS, "192.0.2.10", properties)


def test_archive_properties_of_urls_and_domains() -> None:
    url = build_entity(
        EntityType.URL,
        "https://www.example.org/search",
        {
            "first_captured": "2015-03-04T05:06:07Z",
            "last_captured": "2024-01-02T03:04:05Z",
            "capture_count": 3,
            "last_status": 200,
            "mimetype": "Text/HTML",
            "archive_url": "https://web.archive.org/web/20240102030405/https://www.example.org/search",
            "query_params": ["page", "q"],
        },
    )
    assert url.stored_properties() == {
        "first_captured": "2015-03-04T05:06:07Z",
        "last_captured": "2024-01-02T03:04:05Z",
        "capture_count": 3,
        "last_status": 200,
        "mimetype": "text/html",
        "archive_url": "https://web.archive.org/web/20240102030405/https://www.example.org/search",
        "query_params": ["page", "q"],
    }
    full = {"query_params": [f"p{i}" for i in range(30)], "capture_count": 1}
    assert build_entity(EntityType.URL, "https://example.org/", full).stored_properties()
    domain = build_entity(
        EntityType.DOMAIN,
        "example.org",
        {
            "wayback_first_capture": "2002-01-21T08:50:54Z",
            "wayback_last_capture": "2026-11-19T05:58:12Z",
            "wayback_url_count": 0,
        },
    )
    assert domain.stored_properties()["wayback_url_count"] == 0


@pytest.mark.parametrize(
    "properties",
    [
        {"first_captured": "2015-03-04T05:06:07"},  # naive
        {"capture_count": 0},
        {"last_status": 99},
        {"last_status": 600},
        {"mimetype": "unk"},
        {"archive_url": "https://example.org/web/2024/https://example.org/"},  # not the archive
        {"archive_url": "https://web.archive.org/web/1/" + "a" * 2200},
        {"query_params": ["p"] * 31},
        {"query_params": [""]},
        {"query_params": ["x" * 65]},
    ],
)
def test_invalid_archive_properties_of_urls(properties: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build_entity(EntityType.URL, "https://example.org/", properties)


@pytest.mark.parametrize(
    "properties",
    [
        {"wayback_first_capture": "2002-01-21T08:50:54"},  # naive
        {"wayback_url_count": -1},
    ],
)
def test_invalid_archive_properties_of_domains(properties: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build_entity(EntityType.DOMAIN, "example.org", properties)


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
