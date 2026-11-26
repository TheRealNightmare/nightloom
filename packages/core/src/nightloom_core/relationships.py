"""Relationship types and the (source type, target type) pairs each one allows.

A relationship is only stored when :func:`validate_relationship` accepts it.
The allow-list is explicit so a typo or a confused collector cannot connect,
say, a Person to an IP address with ``RESOLVES_TO``. ``RELATED_TO`` connects
anything to anything but is discouraged: it needs a note explaining the link.
"""

from collections.abc import Iterable
from enum import StrEnum

from nightloom_core.entities import EntityType

E = EntityType


class RelationType(StrEnum):
    """Every relationship type. The value is also the Neo4j relationship type."""

    HAS_SUBDOMAIN = "HAS_SUBDOMAIN"
    RESOLVES_TO = "RESOLVES_TO"
    HAS_MX = "HAS_MX"
    HAS_NS = "HAS_NS"
    # Reverse DNS: the address's PTR records name the target host.
    HAS_PTR = "HAS_PTR"
    # A CNAME: the source name is an alias of (points to) the target name.
    ALIAS_OF = "ALIAS_OF"
    WITHIN = "WITHIN"
    ANNOUNCED_BY = "ANNOUNCED_BY"
    COVERS = "COVERS"
    PRESENTS = "PRESENTS"
    RUNS_ON = "RUNS_ON"
    USES = "USES"
    OWNS = "OWNS"
    # The source organization's account on a code forge holds the target repository.
    HAS_REPOSITORY = "HAS_REPOSITORY"
    # The source account or address authored commits in the target repository.
    CONTRIBUTED_TO = "CONTRIBUTED_TO"
    # The source account committed with the target address (its forge linked them).
    USES_EMAIL = "USES_EMAIL"
    # A likely secret was published in the source repository at the target location.
    HAS_EXPOSURE = "HAS_EXPOSURE"
    # The source account or address has the target public profile.
    HAS_PROFILE = "HAS_PROFILE"
    # The source profile lists the target as another account of its owner.
    LINKED_ACCOUNT = "LINKED_ACCOUNT"
    # The source organization is owned by the target (its parent company), as reported
    # in the source's Legal Entity Identifier record; the note says direct or ultimate.
    SUBSIDIARY_OF = "SUBSIDIARY_OF"
    HOSTS = "HOSTS"
    # The source domain's name looks like the target's (a typo or lookalike of it).
    LOOKALIKE_OF = "LOOKALIKE_OF"
    # The site serves this icon (its favicon).
    HAS_FAVICON = "HAS_FAVICON"
    REGISTERED_BY = "REGISTERED_BY"
    HAS_EMAIL = "HAS_EMAIL"
    # The source breach was of the target's site (its accounts leaked).
    BREACH_OF = "BREACH_OF"
    # The source address is among the accounts in the target breach.
    EXPOSED_IN = "EXPOSED_IN"
    ACCOUNT_OF = "ACCOUNT_OF"
    MENTIONS = "MENTIONS"
    RELATED_TO = "RELATED_TO"


Pair = tuple[EntityType, EntityType]


def _pairs(sources: Iterable[EntityType], targets: Iterable[EntityType]) -> frozenset[Pair]:
    targets = tuple(targets)
    return frozenset((source, target) for source in sources for target in targets)


ALLOWED: dict[RelationType, frozenset[Pair]] = {
    RelationType.HAS_SUBDOMAIN: _pairs([E.DOMAIN], [E.DOMAIN]),
    RelationType.RESOLVES_TO: _pairs([E.DOMAIN], [E.IP_ADDRESS]),
    RelationType.HAS_MX: _pairs([E.DOMAIN], [E.DOMAIN]),
    RelationType.HAS_NS: _pairs([E.DOMAIN], [E.DOMAIN]),
    RelationType.HAS_PTR: _pairs([E.IP_ADDRESS], [E.DOMAIN]),
    RelationType.ALIAS_OF: _pairs([E.DOMAIN], [E.DOMAIN]),
    # An address in a network, or a network inside a larger one.
    RelationType.WITHIN: _pairs([E.IP_ADDRESS, E.NETBLOCK], [E.NETBLOCK]),
    RelationType.ANNOUNCED_BY: _pairs([E.NETBLOCK], [E.ASN]),
    RelationType.COVERS: _pairs([E.CERTIFICATE], [E.DOMAIN]),
    RelationType.PRESENTS: _pairs([E.SERVICE], [E.CERTIFICATE]),
    RelationType.RUNS_ON: _pairs([E.SERVICE], [E.IP_ADDRESS]),
    RelationType.USES: _pairs([E.DOMAIN, E.URL, E.SERVICE], [E.TECHNOLOGY]),
    RelationType.OWNS: _pairs([E.ORGANIZATION], [E.DOMAIN, E.NETBLOCK, E.ASN]),
    RelationType.HAS_REPOSITORY: _pairs([E.ORGANIZATION], [E.REPOSITORY]),
    RelationType.CONTRIBUTED_TO: _pairs([E.USERNAME, E.EMAIL], [E.REPOSITORY]),
    RelationType.USES_EMAIL: _pairs([E.USERNAME], [E.EMAIL]),
    RelationType.HAS_EXPOSURE: _pairs([E.REPOSITORY], [E.SECRET_EXPOSURE]),
    RelationType.HAS_PROFILE: _pairs([E.USERNAME, E.EMAIL], [E.SOCIAL_PROFILE]),
    RelationType.LINKED_ACCOUNT: _pairs([E.SOCIAL_PROFILE], [E.SOCIAL_PROFILE]),
    RelationType.SUBSIDIARY_OF: _pairs([E.ORGANIZATION], [E.ORGANIZATION]),
    RelationType.HOSTS: _pairs([E.DOMAIN], [E.URL]),
    RelationType.LOOKALIKE_OF: _pairs([E.DOMAIN], [E.DOMAIN]),
    RelationType.HAS_FAVICON: _pairs([E.DOMAIN, E.URL], [E.FAVICON]),
    RelationType.REGISTERED_BY: _pairs([E.DOMAIN], [E.ORGANIZATION]),
    RelationType.HAS_EMAIL: _pairs([E.DOMAIN], [E.EMAIL]),
    RelationType.BREACH_OF: _pairs([E.BREACH], [E.DOMAIN]),
    RelationType.EXPOSED_IN: _pairs([E.EMAIL], [E.BREACH]),
    RelationType.ACCOUNT_OF: _pairs([E.USERNAME, E.SOCIAL_PROFILE, E.EMAIL], [E.PERSON]),
    RelationType.MENTIONS: _pairs([E.DOCUMENT], EntityType),
    RelationType.RELATED_TO: _pairs(EntityType, EntityType),
}

# Relationship types that need a note saying why the two entities are linked.
NOTE_REQUIRED = frozenset({RelationType.RELATED_TO})


class RelationshipError(ValueError):
    """A relationship is not allowed. ``str(error)`` is safe to return to a client."""


def is_allowed(relation: RelationType, source: EntityType, target: EntityType) -> bool:
    return (EntityType(source), EntityType(target)) in ALLOWED[RelationType(relation)]


def validate_relationship(
    relation: RelationType,
    source: EntityType,
    target: EntityType,
    note: str | None = None,
) -> None:
    """Raise :class:`RelationshipError` unless ``source -[relation]-> target`` is allowed."""
    relation = RelationType(relation)
    if not is_allowed(relation, source, target):
        raise RelationshipError(
            f"{relation.value} cannot connect a {EntityType(source).value} "
            f"to a {EntityType(target).value}."
        )
    if relation in NOTE_REQUIRED and not (note and note.strip()):
        raise RelationshipError(
            f"{relation.value} needs a note explaining the link; prefer a specific type."
        )


__all__ = [
    "ALLOWED",
    "NOTE_REQUIRED",
    "RelationType",
    "RelationshipError",
    "is_allowed",
    "validate_relationship",
]
