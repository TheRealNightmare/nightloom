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
    USER_PASSWORD_RESET = "user.password_reset"  # noqa: S105 - an action name
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
    # Exporting a case's graph (JSON, GraphML or CSV); never the exported values.
    CASE_EXPORT = "case.export"
    ENTITY_CREATE = "entity.create"
    ENTITY_DELETE = "entity.delete"
    RELATIONSHIP_CREATE = "relationship.create"
    # Saved graph views: only the case and the view's ID (names may be person-related).
    VIEW_CREATE = "view.create"
    VIEW_UPDATE = "view.update"
    VIEW_DELETE = "view.delete"
    # Shuttle jobs: queued by a user, cancelled by a user, finished by the worker.
    JOB_CREATE = "job.create"
    JOB_CANCEL = "job.cancel"
    JOB_FINISH = "job.finish"
    # Clearing cached Shuttle results: an admin (all, or one Shuttle's) or a
    # case owner (that case's). Only counts and the Shuttle name are recorded.
    RESULT_CACHE_CLEAR = "result_cache.clear"
    # Scan profiles (admins) and profile runs (editors and owners).
    SCAN_PROFILE_CREATE = "scan_profile.create"
    SCAN_PROFILE_UPDATE = "scan_profile.update"
    SCAN_PROFILE_DELETE = "scan_profile.delete"
    SCAN_RUN_CREATE = "scan_run.create"
    SCAN_RUN_CANCEL = "scan_run.cancel"
    # Ownership verification: owners add and remove domains; the worker checks
    # them (system:ownership-check), revokes ones whose proof disappeared and
    # expires them after 30 days.
    OWNERSHIP_CREATE = "ownership.create"
    OWNERSHIP_DELETE = "ownership.delete"
    OWNERSHIP_CHECK = "ownership.check"
    OWNERSHIP_REVOKE = "ownership.revoke"
    OWNERSHIP_EXPIRE = "ownership.expire"


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
    VIEW = "view"
    SCAN_PROFILE = "scan_profile"
    SCAN_RUN = "scan_run"
    OWNERSHIP = "ownership"
