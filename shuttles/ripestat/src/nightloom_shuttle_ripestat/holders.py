"""Splitting a RIPEstat AS holder into the network's name and its organization.

RIPEstat names an AS's holder as ``"<network name> - <organization>"``, e.g.
``"CLOUDFLARENET - Cloudflare, Inc."``: the network name as registered for
the AS (``as_name``) and the organization that holds it. :func:`parse_holder`
splits on the **first** ``" - "``, so a dash inside the organization's name
survives (``"EXAMPLE-AS - Example - Networks"`` → ``EXAMPLE-AS``,
``Example - Networks``). Without one, the whole text is the organization.

Each part is trimmed with inner whitespace collapsed, and dropped when it is
empty, has no letter or digit, is longer than :data:`NAME_MAX_LENGTH`, or
looks redacted (:func:`nightloom_sdk.redaction.is_redacted`, the list the
RDAP Shuttle uses too). A dropped organization is counted by the Shuttle,
never quoted.
"""

from dataclasses import dataclass

from nightloom_sdk.redaction import is_redacted

SEPARATOR = " - "
NAME_MAX_LENGTH = 200


@dataclass(frozen=True)
class Holder:
    """What :func:`parse_holder` kept, and whether a part was dropped as redacted."""

    as_name: str | None = None
    organization: str | None = None
    redacted: bool = False


def _clean(text: str) -> tuple[str | None, bool]:
    """``(kept text or None, dropped because it looked redacted)``."""
    cleaned = " ".join(text.split())
    if not cleaned or len(cleaned) > NAME_MAX_LENGTH or not any(c.isalnum() for c in cleaned):
        return None, False
    if is_redacted(cleaned):
        return None, True
    return cleaned, False


def parse_holder(value: object) -> Holder:
    """The network name and organization in a RIPEstat holder. See the module docstring."""
    if not isinstance(value, str):
        return Holder()
    text = " ".join(value.split())
    # Padded, so a holder that starts or ends with the separator ("- Example",
    # "EXAMPLE-AS -") still splits once whitespace has been trimmed.
    left, separator, right = f" {text} ".partition(SEPARATOR)
    if separator:
        as_name, as_name_redacted = _clean(left)
        organization, organization_redacted = _clean(right)
    else:
        as_name, as_name_redacted = None, False
        organization, organization_redacted = _clean(text)
    return Holder(
        as_name=as_name,
        organization=organization,
        redacted=as_name_redacted or organization_redacted,
    )


__all__ = ["NAME_MAX_LENGTH", "SEPARATOR", "Holder", "parse_holder"]
