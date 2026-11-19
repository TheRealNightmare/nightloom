"""Recognizing values that a data source redacted or withheld.

Registries, registrars and routing databases put placeholder text where a
contact or holder was withheld (GDPR redaction, privacy and proxy
services). A Shuttle that keeps organization names skips any value that
:func:`is_redacted` recognizes, so a placeholder never becomes an entity.
Keep :data:`REDACTION_MARKERS` the one list every Shuttle uses.
"""

# Text that marks a withheld value. A value containing any of them, ignoring
# case, is treated as redacted.
REDACTION_MARKERS: tuple[str, ...] = (
    "redacted",
    "redaction",
    "data protected",
    "privacy",
    # Not "private": that is part of many real company names ("... Private Limited").
    "proxy",
    "not disclosed",
    "non-disclosed",
    "non-public",
    "gdpr",
    "withheld",
    "statutory masking",
    "masked",
    "not available",
    "on behalf of",
    "whoisguard",
    "identity protect",
    "contact the registrar",
)


def is_redacted(value: str) -> bool:
    """Whether ``value`` contains a redaction marker (case-insensitive)."""
    folded = value.casefold()
    return any(marker in folded for marker in REDACTION_MARKERS)


__all__ = ["REDACTION_MARKERS", "is_redacted"]
