"""The one source of "now" for authentication code.

Session expiry, ``last_seen`` and timestamps all read the time through
:func:`utcnow`, so tests can move the clock by replacing this function.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)
