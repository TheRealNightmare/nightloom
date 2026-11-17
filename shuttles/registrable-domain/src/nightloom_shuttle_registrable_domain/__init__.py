"""Built-in Shuttle: a domain's registrable domain, computed locally (no network).

Uses the Public Suffix List snapshot bundled with ``tldextract`` through
:func:`nightloom_core.normalize.registrable_domain`, the same code the rest
of Nightloom uses, so it never downloads anything.
"""

from collections.abc import AsyncIterator

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, registrable_domain
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    Finding,
    Mode,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleMeta,
)


class RegistrableDomain(Shuttle):
    """Domain → its registrable domain, linked with ``HAS_SUBDOMAIN``.

    Emits nothing for a domain that is already registrable (an apex such as
    ``example.co.uk``), a public suffix itself (``co.uk``) or a name without
    a known suffix.
    """

    meta = ShuttleMeta(
        name="registrable-domain",
        version="1.0.0",
        title="Registrable domain",
        description=(
            "Finds the registrable domain of a subdomain (www.shop.example.co.uk → "
            "example.co.uk) from the bundled Public Suffix List and links it with "
            "HAS_SUBDOMAIN. Computed locally: no network requests."
        ),
        input_types=frozenset({EntityType.DOMAIN}),
        output_entity_types=frozenset({EntityType.DOMAIN}),
        output_relationship_types=frozenset({RelationType.HAS_SUBDOMAIN}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=False,
        timeout_seconds=30,
        homepage="https://publicsuffix.org/",
        # Derived deterministically from the Public Suffix List: a usually
        # reliable source (the bundled snapshot can lag behind the live list),
        # and the result is confirmed by the list itself.
        default_admiralty="B1",
        default_confidence=0.95,
    )

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        try:
            parent = registrable_domain(entity.value)
        except NormalizationError:
            ctx.logger.info("The input is not a domain name this Shuttle can use.")
            return
        if parent is None:
            ctx.logger.info("No registrable domain: a public suffix or an unknown suffix.")
            return
        if parent == entity.value:
            return  # already the registrable domain
        yield EntityFinding(type=EntityType.DOMAIN, value=parent)
        yield RelationshipFinding(
            type=RelationType.HAS_SUBDOMAIN,
            source=EntityRef(type=EntityType.DOMAIN, value=parent),
            target=entity.ref,
        )


__all__ = ["RegistrableDomain"]
