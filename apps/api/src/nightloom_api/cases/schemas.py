"""Request and response bodies for cases and case members.

Free-text reasons (``person_entities_reason``, ``change_reason``) end up in
audit events, whose strings are cut at 512 characters, so they are limited to
:data:`REASON_MAX_LENGTH`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from nightloom_api.auth.models import Role
from nightloom_api.auth.schemas import Email
from nightloom_api.cases.models import (
    DESCRIPTION_MAX_LENGTH,
    LEGAL_BASES_NEEDING_NOTE,
    LEGAL_BASIS_NOTE_MAX_LENGTH,
    PURPOSE_MAX_LENGTH,
    PURPOSE_MIN_LENGTH,
    RETENTION_DAYS_MAX,
    RETENTION_DAYS_MIN,
    TITLE_MAX_LENGTH,
    TITLE_MIN_LENGTH,
    Case,
    CaseRole,
    CaseStatus,
    LegalBasis,
)

PERSON_ENTITIES_REASON_MIN_LENGTH = 30
CHANGE_REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500

ETHICS_NOT_ACKNOWLEDGED = "You must acknowledge ETHICS.md (ethics_acknowledged: true)."
NOTE_REQUIRED = "legal_basis_note is required when legal_basis is authorized_engagement or other."
REASON_REQUIRED = (
    f"person_entities_reason (at least {PERSON_ENTITIES_REASON_MIN_LENGTH} characters) "
    "is required when allow_person_entities is true."
)

Title = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=TITLE_MIN_LENGTH, max_length=TITLE_MAX_LENGTH
    ),
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH)
]
Purpose = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=PURPOSE_MIN_LENGTH, max_length=PURPOSE_MAX_LENGTH
    ),
]
LegalBasisNote = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=LEGAL_BASIS_NOTE_MAX_LENGTH)
]
RetentionDays = Annotated[int, Field(ge=RETENTION_DAYS_MIN, le=RETENTION_DAYS_MAX)]
PersonEntitiesReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=PERSON_ENTITIES_REASON_MIN_LENGTH,
        max_length=REASON_MAX_LENGTH,
    ),
]
ChangeReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=CHANGE_REASON_MIN_LENGTH, max_length=REASON_MAX_LENGTH
    ),
]


def blank_to_none(value: str | None) -> str | None:
    return value or None


def note_missing(legal_basis: LegalBasis, note: str | None) -> bool:
    return legal_basis in LEGAL_BASES_NEEDING_NOTE and not note


class CaseCreate(BaseModel):
    """Body of ``POST /cases``."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description | None = None
    purpose: Purpose
    legal_basis: LegalBasis
    legal_basis_note: LegalBasisNote | None = None
    ethics_acknowledged: bool
    allow_person_entities: bool = False
    # Kept only in the case.create audit event, never on the case.
    person_entities_reason: PersonEntitiesReason | None = None
    retention_days: RetentionDays | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        self.description = blank_to_none(self.description)
        self.legal_basis_note = blank_to_none(self.legal_basis_note)
        if not self.ethics_acknowledged:
            raise ValueError(ETHICS_NOT_ACKNOWLEDGED)
        if note_missing(self.legal_basis, self.legal_basis_note):
            raise ValueError(NOTE_REQUIRED)
        if self.allow_person_entities and self.person_entities_reason is None:
            raise ValueError(REASON_REQUIRED)
        return self


# Fields an editor may change; every other field of CaseUpdate needs an owner.
EDITOR_FIELDS = frozenset({"title", "description"})
# Changing one of these needs a change_reason.
REASON_FIELDS = frozenset({"purpose", "legal_basis", "legal_basis_note", "allow_person_entities"})
# May be omitted, but not set to null.
NOT_NULLABLE = frozenset(
    {
        "title",
        "purpose",
        "legal_basis",
        "status",
        "allow_person_entities",
        "allow_auto_pivot",
        "allow_site_visits",
    }
)


class CaseUpdate(BaseModel):
    """Body of ``PATCH /cases/{id}``. Omitted fields are left unchanged.

    ``description``, ``legal_basis_note`` and ``retention_days`` may be set to
    null to clear them.
    """

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    purpose: Purpose | None = None
    legal_basis: LegalBasis | None = None
    legal_basis_note: LegalBasisNote | None = None
    status: CaseStatus | None = None
    allow_person_entities: bool | None = None
    # Owners only: whether profile runs may pivot (continue from what they find).
    allow_auto_pivot: bool | None = None
    # Owners only: whether Shuttles may visit sites' public pages, like a browser.
    allow_site_visits: bool | None = None
    retention_days: RetentionDays | None = None
    change_reason: ChangeReason | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        for field in sorted(NOT_NULLABLE & self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        # Assigning marks a field as sent, so touch only the ones that were.
        if "description" in self.model_fields_set:
            self.description = blank_to_none(self.description)
        if "legal_basis_note" in self.model_fields_set:
            self.legal_basis_note = blank_to_none(self.legal_basis_note)
        return self

    def changes(self) -> dict[str, Any]:
        """The fields that were sent (excluding ``change_reason``), with their values."""
        return {
            field: getattr(self, field)
            for field in self.model_fields_set
            if field != "change_reason"
        }


class CaseDelete(BaseModel):
    """Body of ``DELETE /cases/{id}``: the case's exact title, as a confirmation."""

    model_config = ConfigDict(extra="forbid")

    confirm_title: str


class CaseOut(BaseModel):
    """A case, with the caller's own (effective) role in it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    purpose: str
    legal_basis: LegalBasis
    legal_basis_note: str | None
    ethics_ack_version: str
    ethics_ack_at: datetime
    allow_person_entities: bool
    allow_auto_pivot: bool
    allow_site_visits: bool
    status: CaseStatus
    retention_days: int | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    my_role: CaseRole

    @classmethod
    def build(cls, case: Case, role: CaseRole) -> "CaseOut":
        fields = {name: getattr(case, name) for name in cls.model_fields if name != "my_role"}
        return cls.model_validate({**fields, "my_role": role})


class CasePage(BaseModel):
    """Most recently updated first. Pass ``next_cursor`` as ``cursor`` for the next page."""

    items: list[CaseOut]
    next_cursor: str | None


class MemberPut(BaseModel):
    """Body of ``PUT /cases/{id}/members``: add a user by email, or change their role."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    role: CaseRole


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    global_role: Role
    is_active: bool
    role: CaseRole
    added_by: uuid.UUID | None
    added_at: datetime
