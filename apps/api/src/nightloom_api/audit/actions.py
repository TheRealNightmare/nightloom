"""What can be recorded in the audit log.

``action`` is stored as plain text and validated against :class:`AuditAction`
in code, so adding an action needs no migration.
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
    CASE_CREATE = "case.create"
    CASE_UPDATE = "case.update"
    CASE_STATUS_CHANGE = "case.status_change"
    CASE_DELETE = "case.delete"
    CASE_MEMBER_ADD = "case.member_add"
    CASE_MEMBER_UPDATE = "case.member_update"
    CASE_MEMBER_REMOVE = "case.member_remove"
    # Removing a deleted case's graph from Neo4j, after the case row is gone.
    CASE_GRAPH_DELETE = "case.graph_delete"
    ENTITY_CREATE = "entity.create"
    ENTITY_DELETE = "entity.delete"
    RELATIONSHIP_CREATE = "relationship.create"
    # Shuttle jobs: queued by a user, cancelled by a user, finished by the worker.
    JOB_CREATE = "job.create"
    JOB_CANCEL = "job.cancel"
    JOB_FINISH = "job.finish"


class AuditOutcome(StrEnum):
    """How the action ended. Also enforced by a CHECK constraint."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class TargetType(StrEnum):
    """What an event's ``target_id`` refers to."""

    USER = "user"
    CASE = "case"
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    JOB = "job"
