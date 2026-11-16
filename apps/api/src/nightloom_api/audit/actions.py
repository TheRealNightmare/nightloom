"""What can be recorded in the audit log.

``action`` is stored as plain text and validated against :class:`AuditAction`
in code, so adding an action (``case.*`` next) needs no migration.
"""

from enum import StrEnum


class AuditAction(StrEnum):
    """Every action the audit log knows about."""

    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_PASSWORD_CHANGE = "auth.password_change"  # noqa: S105 - an action name
    AUTH_RATE_LIMITED = "auth.rate_limited"
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_SESSIONS_REVOKE = "user.sessions_revoke"
    AUTHZ_DENIED = "authz.denied"
    AUDIT_VERIFY = "audit.verify"


class AuditOutcome(StrEnum):
    """How the action ended. Also enforced by a CHECK constraint."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class TargetType(StrEnum):
    """What an event's ``target_id`` refers to."""

    USER = "user"
