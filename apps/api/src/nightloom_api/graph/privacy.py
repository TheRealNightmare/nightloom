"""How entity keys appear in audit events.

Audit events outlive the case, so person-related keys (an email address, a
name, a phone number) are recorded only as their SHA-256: enough to correlate
events, without keeping the personal data after the case's graph is deleted.
"""

import hashlib

from nightloom_core.entities import Sensitivity


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def key_details(key: str, sensitivity: Sensitivity, name: str = "key") -> dict[str, str]:
    """``{name: key}``, or ``{name_sha256: digest}`` for a person-related key."""
    if sensitivity == Sensitivity.PERSON:
        return {f"{name}_sha256": digest(key)}
    return {name: key}


def audit_target(text: str, *, personal: bool) -> str:
    return f"sha256:{digest(text)}" if personal else text


__all__ = ["audit_target", "digest", "key_details"]
