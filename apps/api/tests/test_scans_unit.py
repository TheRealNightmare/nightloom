"""Scan profiles and run planning, without a database."""

from typing import Any

import pytest

from nightloom_api.scans import profiles
from nightloom_api.scans.profiles import BUILT_IN_PROFILES, Profile, ProfileError
from nightloom_api.scans.service import plan_run
from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import EntityInput, Finding, Mode, Shuttle, ShuttleContext, ShuttleMeta
from nightloom_sdk.registry import ShuttleRegistry, default_registry


def _meta(name: str, **overrides: Any) -> ShuttleMeta:
    values: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "title": f"Test {name}",
        "description": "A Shuttle for the scan profile tests.",
        "input_types": {EntityType.DOMAIN},
        "output_entity_types": {EntityType.DOMAIN},
        "output_relationship_types": {RelationType.HAS_SUBDOMAIN},
        "sensitivity": Sensitivity.INFRASTRUCTURE,
        "network": False,
    }
    values.update(overrides)
    return ShuttleMeta.model_validate(values)


class _Nothing(Shuttle):
    async def run(self, ctx: ShuttleContext, entity: Any) -> Any:  # pragma: no cover
        return
        yield


class Domains(_Nothing):
    meta = _meta("domains")


class Addresses(_Nothing):
    meta = _meta(
        "addresses",
        input_types={EntityType.IP_ADDRESS},
        output_entity_types={EntityType.NETBLOCK},
        output_relationship_types={RelationType.WITHIN},
    )


class Emails(_Nothing):
    meta = _meta(
        "emails",
        output_entity_types={EntityType.EMAIL},
        output_relationship_types={RelationType.HAS_EMAIL},
        sensitivity=Sensitivity.PERSON,
    )


class Active(_Nothing):
    meta = _meta("active", mode=Mode.ACTIVE)


class Visit(_Nothing):
    meta = _meta("visit", mode=Mode.VISIT)


REGISTRY = ShuttleRegistry([Domains, Addresses, Emails, Active, Visit])
PROFILE = Profile(
    id="p",
    name="P",
    description="",
    shuttles=("domains", "addresses", "emails", "gone"),
    built_in=False,
)
_ = (EntityInput, Finding)


def test_built_in_profiles_use_installed_passive_shuttles() -> None:
    registry = default_registry()
    assert [p.id for p in BUILT_IN_PROFILES] == [
        "quick-domain-recon",
        "deep-domain-recon",
        "ip-network-context",
        "exposure-check",
    ]
    assert len({p.name.lower() for p in BUILT_IN_PROFILES}) == len(BUILT_IN_PROFILES)
    for profile in BUILT_IN_PROFILES:
        assert profile.built_in
        assert profiles.missing_shuttles(profile, registry) == [], profile.id
        assert profiles.check_shuttles(profile.shuttles, registry) == profile.shuttles
        assert profiles.is_built_in(profile.id)
    quick, deep, network, exposure = BUILT_IN_PROFILES
    assert EntityType.DOMAIN in profiles.accepted_types(quick, registry)
    assert EntityType.IP_ADDRESS in profiles.accepted_types(network, registry)
    assert EntityType.ASN in profiles.accepted_types(network, registry)
    assert profiles.accepted_types(exposure, registry) == sorted(
        {EntityType.IP_ADDRESS, EntityType.NETBLOCK}
    )
    assert deep.shuttles[0] == "dns"


def test_names_and_descriptions_are_cleaned() -> None:
    assert profiles.clean_name("  Mail   servers ") == "Mail servers"
    for bad in ("", "   ", "x" * 61, "tab\x07"):
        with pytest.raises(ProfileError):
            profiles.clean_name(bad)
    assert profiles.clean_description(" A\n quick  look ") == "A quick look"
    with pytest.raises(ProfileError):
        profiles.clean_description("x" * 301)


def test_shuttle_lists_are_checked() -> None:
    assert profiles.check_shuttles(["domains", "addresses", "domains"], REGISTRY) == (
        "domains",
        "addresses",
    )
    with pytest.raises(ProfileError, match="1 to 15"):
        profiles.check_shuttles([], REGISTRY)
    with pytest.raises(ProfileError, match="1 to 15"):
        profiles.check_shuttles([f"s{i}" for i in range(16)], REGISTRY)
    with pytest.raises(ProfileError, match="Unknown Shuttle: gone"):
        profiles.check_shuttles(["gone"], REGISTRY)
    with pytest.raises(ProfileError, match="active Shuttle"):
        profiles.check_shuttles(["active"], REGISTRY)
    # A Shuttle that visits the site may be in a profile.
    assert profiles.check_shuttles(["visit"], REGISTRY) == ("visit",)


def test_accepted_types_and_missing_shuttles() -> None:
    assert profiles.accepted_types(PROFILE, REGISTRY) == sorted(
        {EntityType.DOMAIN, EntityType.IP_ADDRESS}
    )
    assert profiles.missing_shuttles(PROFILE, REGISTRY) == ["gone"]


def test_plans_go_entity_by_entity_and_skip_what_cannot_run() -> None:
    entities = [
        ("domain:example.org", EntityType.DOMAIN),
        ("ip:192.0.2.1", EntityType.IP_ADDRESS),
    ]
    plan = plan_run(PROFILE, entities, REGISTRY, allow_person_entities=False)
    assert plan.items == [
        {"shuttle": "domains", "entity_key": "domain:example.org"},
        {"shuttle": "addresses", "entity_key": "ip:192.0.2.1"},
    ]
    assert plan.skipped == {
        "not_accepted": 3,  # addresses on a domain; domains and emails on an address
        "person_entities_disabled": 1,  # emails on the domain
        "not_installed": 2,  # gone, for each entity
    }
    allowed = plan_run(PROFILE, entities, REGISTRY, allow_person_entities=True)
    assert {"shuttle": "emails", "entity_key": "domain:example.org"} in allowed.items
    assert allowed.skipped["person_entities_disabled"] == 0


def test_visits_are_planned_only_where_allowed() -> None:
    visiting = Profile(
        id="v", name="V", description="", shuttles=("domains", "visit"), built_in=False
    )
    entities = [("domain:example.org", EntityType.DOMAIN)]
    plan = plan_run(visiting, entities, REGISTRY, allow_person_entities=False)
    assert [item["shuttle"] for item in plan.items] == ["domains"]
    assert plan.skipped == {"site_visits_disabled": 1}
    allowed = plan_run(
        visiting, entities, REGISTRY, allow_person_entities=False, allow_site_visits=True
    )
    assert [item["shuttle"] for item in allowed.items] == ["domains", "visit"]
    assert profiles.accepted_types(visiting, REGISTRY) == [EntityType.DOMAIN]
