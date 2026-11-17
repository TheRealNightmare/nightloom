"""The registrable-domain Shuttle: no network, Public Suffix List from the bundled snapshot."""

import pytest

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityFinding, EntityInput, EntityRef, Mode, RelationshipFinding
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context
from nightloom_shuttle_registrable_domain import RegistrableDomain


async def findings_for(value: str) -> list[object]:
    entity = entity_input(EntityType.DOMAIN, value)
    found = await collect(RegistrableDomain(), entity)
    assert_declared(RegistrableDomain.meta, found, entity)
    return list(found)


def expected(parent: str, child: str) -> list[object]:
    return [
        EntityFinding(type=EntityType.DOMAIN, value=parent),
        RelationshipFinding(
            type=RelationType.HAS_SUBDOMAIN,
            source=EntityRef(type=EntityType.DOMAIN, value=parent),
            target=EntityRef(type=EntityType.DOMAIN, value=child),
        ),
    ]


def test_meta() -> None:
    meta = RegistrableDomain.meta
    assert meta.name == "registrable-domain"
    assert meta.mode == Mode.PASSIVE
    assert meta.sensitivity == Sensitivity.INFRASTRUCTURE
    assert meta.network is False
    assert meta.rate_limit is None
    assert meta.input_types == {EntityType.DOMAIN}
    assert meta.output_entity_types == {EntityType.DOMAIN}
    assert meta.output_relationship_types == {RelationType.HAS_SUBDOMAIN}
    assert meta.source == "shuttle:registrable-domain@1.0.0"


def test_registered_through_its_entry_point() -> None:
    assert ShuttleRegistry.discover().get("registrable-domain") is RegistrableDomain


@pytest.mark.parametrize(
    ("value", "parent"),
    [
        ("www.example.org", "example.org"),
        ("www.shop.example.co.uk", "example.co.uk"),  # multi-level suffix
        ("a.b.c.d.example.com", "example.com"),
        ("api.example.com.au", "example.com.au"),
        # Wildcard rule *.kawasaki.jp: example.kawasaki.jp is itself a suffix...
        ("www.example.kawasaki.jp", "www.example.kawasaki.jp"),
        ("a.www.example.kawasaki.jp", "www.example.kawasaki.jp"),
        # ...except for the exception rule !city.kawasaki.jp.
        ("www.city.kawasaki.jp", "city.kawasaki.jp"),
        # Only the ICANN section of the list is used (as everywhere in
        # nightloom_core): private suffixes such as github.io are not suffixes.
        ("docs.project.github.io", "github.io"),
        ("_dmarc.example.org", "example.org"),
        ("WWW.Example.ORG.", "example.org"),  # normalized first
    ],
)
async def test_subdomains_get_their_registrable_domain(value: str, parent: str) -> None:
    child = entity_input(EntityType.DOMAIN, value).value
    found = await findings_for(value)
    if parent == child:
        assert found == []
    else:
        assert found == expected(parent, child)


@pytest.mark.parametrize(
    ("value", "parent", "child"),
    [
        # IDNs are stored in A-label form; the suffix lookup works on them.
        ("www.bücher.example", None, None),  # .example is not a public suffix
        ("www.bücher.de", "xn--bcher-kva.de", "www.xn--bcher-kva.de"),
        ("shop.пример.рф", "xn--e1afmkfd.xn--p1ai", "shop.xn--e1afmkfd.xn--p1ai"),
        ("www.例え.jp", "xn--r8jz45g.jp", "www.xn--r8jz45g.jp"),
        ("WWW.Bücher.DE", "xn--bcher-kva.de", "www.xn--bcher-kva.de"),
    ],
)
async def test_internationalized_domains(value: str, parent: str | None, child: str | None) -> None:
    found = await findings_for(value)
    if parent is None:
        assert found == []
    else:
        assert child is not None
        assert found == expected(parent, child)


@pytest.mark.parametrize(
    "value",
    [
        "example.org",  # already registrable
        "example.co.uk",
        "xn--bcher-kva.de",
        "co.uk",  # a public suffix itself
        "com.au",
        "intranet.invalid",  # no known suffix
        "host.localdomain",
    ],
)
async def test_nothing_for_apex_domains_and_suffixes(value: str) -> None:
    assert await findings_for(value) == []


async def test_wildcard_domains_link_to_their_registrable_domain() -> None:
    assert await findings_for("*.shop.example.co.uk") == expected(
        "example.co.uk", "*.shop.example.co.uk"
    )
    assert await findings_for("*.example.co.uk") == expected("example.co.uk", "*.example.co.uk")


async def test_input_that_is_not_a_domain_yields_nothing() -> None:
    # The runner only passes stored domains; be safe anyway.
    entity = EntityInput(type=EntityType.DOMAIN, value="not a domain", key="domain:x")
    ctx = fake_context(RegistrableDomain.meta)
    assert [f async for f in RegistrableDomain().run(ctx, entity)] == []


async def test_never_touches_the_network() -> None:
    ctx = fake_context(RegistrableDomain.meta)
    with pytest.raises(RuntimeError):
        _ = ctx.http
