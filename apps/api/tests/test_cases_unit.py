"""Case helpers that need no services: capabilities, LIKE escaping, cursors, schemas."""

import base64
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nightloom_api.auth.models import Role
from nightloom_api.cases import service
from nightloom_api.cases.access import (
    CAPABILITIES,
    Capability,
    allowed_case_roles,
    can,
    effective_role,
)
from nightloom_api.cases.models import Case, CaseMember, CaseRole, CaseStatus, LegalBasis
from nightloom_api.cases.schemas import CaseUpdate

VALID_PURPOSE = "Map the public attack surface of example.org."


def test_capabilities() -> None:
    assert CAPABILITIES[CaseRole.VIEWER] == {Capability.READ}
    assert can(CaseRole.EDITOR, Capability.EDIT)
    assert can(CaseRole.EDITOR, Capability.RUN_SHUTTLES)
    assert can(CaseRole.EDITOR, Capability.READ_AUDIT)
    assert not can(CaseRole.EDITOR, Capability.MANAGE)
    assert CAPABILITIES[CaseRole.OWNER] == set(Capability)


@pytest.mark.parametrize("case_role", list(CaseRole))
def test_global_viewers_are_capped(case_role: CaseRole) -> None:
    assert effective_role(Role.VIEWER, case_role) == CaseRole.VIEWER
    assert effective_role(Role.ANALYST, case_role) == case_role
    assert effective_role(Role.ADMIN, case_role) == case_role


def test_allowed_case_roles() -> None:
    assert allowed_case_roles(Role.VIEWER) == {CaseRole.VIEWER}
    assert allowed_case_roles(Role.ANALYST) == set(CaseRole)


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("plain", "plain"),
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("\\%_", "\\\\\\%\\_"),
    ],
)
def test_escape_like(raw: str, escaped: str) -> None:
    assert service.escape_like(raw) == escaped


def test_cursor_round_trip() -> None:
    stamp = datetime(2026, 11, 16, 12, 30, 45, 123456, tzinfo=UTC)
    case_id = uuid.uuid4()

    cursor = service.encode_cursor(stamp, case_id)

    assert "=" not in cursor
    assert service.decode_cursor(cursor) == (stamp, case_id)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!",
        _b64("2026-11-16T12:00:00|not-a-uuid"),
        _b64(f"2026-11-16T12:00:00|{uuid.uuid4()}"),  # naive timestamp
        _b64(f"yesterday|{uuid.uuid4()}"),
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
    ],
)
def test_bad_cursors(cursor: str) -> None:
    with pytest.raises(service.InvalidCursorError, match="Invalid cursor"):
        service.decode_cursor(cursor)


def test_update_tracks_only_sent_fields() -> None:
    body = CaseUpdate.model_validate({"title": "  New title  ", "change_reason": "x" * 10})

    assert body.changes() == {"title": "New title"}


def test_update_can_clear_nullable_fields() -> None:
    body = CaseUpdate.model_validate({"description": "", "legal_basis_note": None})

    assert body.changes() == {"description": None, "legal_basis_note": None}


@pytest.mark.parametrize("field", ["title", "purpose", "legal_basis", "status"])
def test_update_rejects_null_for_required_fields(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field} cannot be null"):
        CaseUpdate.model_validate({field: None})


def _case(**overrides: object) -> Case:
    values: dict[str, object] = {
        "title": "Title",
        "description": None,
        "purpose": VALID_PURPOSE,
        "legal_basis": LegalBasis.OWNED_ASSETS,
        "legal_basis_note": None,
        "status": CaseStatus.OPEN,
        "allow_person_entities": False,
        "retention_days": None,
    }
    values.update(overrides)
    return Case(**values)


def test_plan_update() -> None:
    case = _case()

    editor_plan = service.plan_update(case, CaseUpdate(title="Title", description="New"))
    owner_plan = service.plan_update(case, CaseUpdate(purpose=VALID_PURPOSE))

    assert editor_plan.changes == {"description": "New"}
    assert not editor_plan.needs_owner
    # Unchanged, but still an owner-only field.
    assert owner_plan.changes == {}
    assert owner_plan.needs_owner


def test_reprs_hold_no_case_text() -> None:
    case = _case(id=uuid.uuid4())
    member = CaseMember(case_id=case.id, user_id=uuid.uuid4(), role=CaseRole.OWNER)

    assert VALID_PURPOSE not in repr(case)
    assert "Title" not in repr(case)
    assert str(case.id) in repr(case)
    assert "owner" in repr(member)


def test_service_errors_have_fixed_reasons() -> None:
    assert service.ConfirmationError().reason == "confirm_title_mismatch"
    assert service.ConflictError("x").reason == "last_owner"
    assert service.NotFoundError("x").reason == "not_found"
    assert service.CaseValidationError("x").reason == "invalid"
    assert service.InvalidCursorError().reason == "invalid_cursor"
