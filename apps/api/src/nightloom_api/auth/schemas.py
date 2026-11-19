"""Request and response bodies for authentication and user management.

Password fields carry no length constraints here: the policy is checked in
:mod:`nightloom_api.auth.service`, whose errors never quote the password.
"""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

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
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    """Body of a successful sign-in; the session itself travels in cookies."""

    user: UserOut


class UserCreate(BaseModel):
    """Body of ``POST /users`` (admin only).

    By default the server generates a temporary password and returns it once
    (``generate_password``). With ``generate_password: false`` the admin
    supplies ``password`` instead. Either way the user must choose a new
    password at their first sign-in unless ``must_change_password`` is false.
    """

    model_config = ConfigDict(extra="forbid")

    email: Email
    display_name: DisplayName
    role: Role
    generate_password: bool = True
    password: str | None = None
    must_change_password: bool = True

    @model_validator(mode="after")
    def _password_xor_generated(self) -> "UserCreate":
        if self.generate_password and self.password is not None:
            raise ValueError("Leave out the password when the server generates one.")
        if not self.generate_password and self.password is None:
            raise ValueError("Enter a password, or let the server generate one.")
        return self


class UserCreated(UserOut):
    """Body of a successful ``POST /users``.

    ``temporary_password`` is the generated password, shown this once and
    never stored in plain text; ``null`` when the admin supplied the password.
    """

    temporary_password: str | None


class PasswordResetOut(BaseModel):
    """Body of ``POST /users/{id}/password-reset``: the new temporary password, once."""

    user: UserOut
    temporary_password: str
    revoked_sessions: int


class UserUpdate(BaseModel):
    """Body of ``PATCH /users/{id}`` (admin only). Omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName | None = None
    role: Role | None = None
    is_active: bool | None = Field(default=None)
