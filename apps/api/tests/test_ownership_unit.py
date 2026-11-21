"""Ownership verification without a database: domains, targets, coverage."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nightloom_api.ownership import service
from nightloom_api.ownership.models import OwnershipVerification

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def row(domain: str, status: str = "verified", **overrides: object) -> OwnershipVerification:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "domain": domain,
        "method": "dns_txt",
        "token": "t",
        "status": status,
        "verified_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=29),
        "addresses": [],
    }
    values.update(overrides)
    return OwnershipVerification(**values)


def test_clean_domain() -> None:
    assert service.clean_domain(" Shop.Example.ORG. ") == "shop.example.org"
    assert service.clean_domain("bücher.de") == "xn--bcher-kva.de"
    for bad, message in [
        ("co.uk", "public suffix"),
        ("*.example.org", "wildcard"),
        ("_dmarc.example.org", "service label"),
        ("localhost", "two labels"),
        ("exa mple.org", "."),
    ]:
        with pytest.raises(service.OwnershipError, match=message):
            service.clean_domain(bad)


def test_proof_locations() -> None:
    assert service.proof_value("abc") == "nightloom-verification=abc"
    assert service.record_name("example.org") == "_nightloom-challenge.example.org"
    assert service.file_url("example.org") == (
        "https://example.org/.well-known/nightloom-verification.txt"
    )
    assert len(service.new_token()) >= 32
    assert service.new_token() != service.new_token()


def test_targets() -> None:
    target = service.target_of
    assert target("domain:www.example.org") == service.Target(host="www.example.org")
    assert target("ip_address:192.0.2.1") == service.Target(address="192.0.2.1")
    assert target("url:https://www.example.org/login?x") == service.Target(host="www.example.org")
    assert target("url:http://192.0.2.1:8080/") == service.Target(address="192.0.2.1")
    assert target("service:192.0.2.1:443/tcp") == service.Target(address="192.0.2.1")
    assert target("service:[2001:db8::1]:53/udp") == service.Target(address="2001:db8::1")
    assert target("asn:64500") is None
    assert target("email:alex@example.org") is None
    assert target("nonsense") is None


def test_coverage() -> None:
    apex = row("example.org", addresses=["192.0.2.1"])
    shop = row("shop.example.org")
    rows = [apex, shop]
    covering = service.covering
    assert covering(rows, service.Target(host="example.org"), NOW) is apex
    assert covering(rows, service.Target(host="a.b.example.org"), NOW) is apex
    # The most specific verified domain wins.
    assert covering(rows, service.Target(host="www.shop.example.org"), NOW) is shop
    assert covering(rows, service.Target(host="badexample.org"), NOW) is None
    assert covering(rows, service.Target(host="example.org.evil.test"), NOW) is None
    assert covering(rows, service.Target(address="192.0.2.1"), NOW) is apex
    assert covering(rows, service.Target(address="192.0.2.2"), NOW) is None
    # Only verified, unexpired verifications count.
    for inactive in (
        row("example.org", "pending", verified_at=None, expires_at=None),
        row("example.org", "revoked", verified_at=None, expires_at=None),
        row("example.org", expires_at=NOW),
    ):
        assert covering([inactive], service.Target(host="example.org"), NOW) is None
