"""Password hashing (Argon2id) and the password policy."""

from functools import lru_cache

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

MIN_LENGTH = 12
MAX_LENGTH = 128
# Local parts shorter than this are not checked for containment: a one- or
# two-letter local part would forbid most passwords for no real gain.
MIN_LOCAL_PART_TO_CHECK = 3

# Argon2id with argon2-cffi's recommended parameters (RFC 9106 low-memory profile).
_hasher = PasswordHash((Argon2Hasher(),))


class PasswordPolicyError(ValueError):
    """The password does not meet the policy. The message is safe to show to users."""


def validate_password(password: str, email: str) -> None:
    """Raise :class:`PasswordPolicyError` unless ``password`` meets the policy.

    12-128 characters, and neither equal to nor containing the email's local
    part (case-insensitive).
    """
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_LENGTH} characters long.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_LENGTH} characters long.")
    local_part = email.strip().lower().partition("@")[0]
    lowered = password.lower()
    if local_part and (
        lowered == local_part
        or (len(local_part) >= MIN_LOCAL_PART_TO_CHECK and local_part in lowered)
    ):
        raise PasswordPolicyError("Password must not contain your email address.")


def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password`` (with a random salt)."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Check ``password`` against ``password_hash``.

    Returns ``(valid, new_hash)``. ``new_hash`` is set when the password is
    valid but the stored hash uses outdated parameters and should be replaced.
    A malformed hash counts as a mismatch.
    """
    try:
        return _hasher.verify_and_update(password, password_hash)
    except Exception:
        return False, None


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("nightloom-dummy-password-for-timing")


def dummy_verify(password: str) -> None:
    """Spend as long as a real verification, for logins with an unknown email.

    Without it, "no such user" would answer measurably faster than "wrong
    password" and reveal which emails have accounts.
    """
    _hasher.verify(password, _dummy_hash())
