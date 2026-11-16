"""Request and response bodies for authentication and user management.

Password fields carry no length constraints here: the policy is checked in
:mod:`nightloom_api.auth.service`, whose errors never quote the password.
"""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from nightloom_api.auth.models import DISPLAY_NAME_MAX_LENGTH, EMAIL_MAX_LENGTH, Role

# Deliberately loose: one "@", no whitespace, a dot in the domain. Deliverability
# is not checked; administrators type these addresses by hand.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    """Strip surrounding whitespace and lower-case the address."""
    return email.strip().lower()


def _valid_email(email: str) -> str:
    normalized = normalize_email(email)
    if len(normalized) > EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized


Email = Annotated[str, AfterValidator(_valid_email)]
DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH),
]


class LoginRequest(BaseModel):
    """Credentials for ``POST /auth/login``. The email is normalized, not validated."""

    email: str
    password: str


class PasswordChangeRequest(BaseModel):
    """Body of ``POST /auth/password``."""

    current_password: str
    new_password: str


class UserOut(BaseModel):
    """A user as returned by the API. Never includes the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    """Body of a successful sign-in; the session itself travels in cookies."""

    user: UserOut


class UserCreate(BaseModel):
    """Body of ``POST /users`` (admin only)."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    display_name: DisplayName
    role: Role
    password: str


class UserUpdate(BaseModel):
    """Body of ``PATCH /users/{id}`` (admin only). Omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    role: Role | None = None
    is_active: bool | None = Field(default=None)
