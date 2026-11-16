"""Password hashing (Argon2id) and the password policy.

Argon2 is deliberately slow (about a tenth of a second of CPU per hash), so
request handlers must use the ``*_async`` variants: they run the hash in a
worker thread instead of on the event loop, at most
:data:`DEFAULT_HASH_CONCURRENCY` (``NIGHTLOOM_PASSWORD_HASH_CONCURRENCY``) at a
time, so a flood of sign-ins can neither freeze other requests nor take every
worker thread and CPU core. The synchronous functions remain for the CLI.
"""

from functools import lru_cache

import anyio
import anyio.to_thread
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

MIN_LENGTH = 12
MAX_LENGTH = 128
# Local parts shorter than this are not checked for containment: a one- or
# two-letter local part would forbid most passwords for no real gain.
MIN_LOCAL_PART_TO_CHECK = 3

# Argon2id with argon2-cffi's recommended parameters (RFC 9106 low-memory profile).
_hasher = PasswordHash((Argon2Hasher(),))

DEFAULT_HASH_CONCURRENCY = 4
# Shared by every request; sized from the settings by :func:`set_hash_concurrency`.
_hash_limiter = anyio.CapacityLimiter(DEFAULT_HASH_CONCURRENCY)


def set_hash_concurrency(limit: int) -> None:
    """Allow at most ``limit`` password hashes to run at the same time."""
    if limit < 1:
        raise ValueError("password hash concurrency must be at least 1")
    _hash_limiter.total_tokens = limit


def hash_concurrency() -> int:
    """How many password hashes may run at the same time."""
    return int(_hash_limiter.total_tokens)


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


async def hash_password_async(password: str) -> str:
    """:func:`hash_password` in a worker thread, bounded by the hash limiter."""
    return await anyio.to_thread.run_sync(hash_password, password, limiter=_hash_limiter)


async def verify_password_async(password: str, password_hash: str) -> tuple[bool, str | None]:
    """:func:`verify_password` in a worker thread, bounded by the hash limiter."""
    return await anyio.to_thread.run_sync(
        verify_password, password, password_hash, limiter=_hash_limiter
    )


async def dummy_verify_async(password: str) -> None:
    """:func:`dummy_verify` in a worker thread, bounded by the hash limiter."""
    await anyio.to_thread.run_sync(dummy_verify, password, limiter=_hash_limiter)
