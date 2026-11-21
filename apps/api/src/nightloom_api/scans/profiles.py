"""Scan profiles: named lists of Shuttles to run together.

Four profiles are **built in** (defined here, the same on every instance, not
editable); admins add their own (table ``scan_profiles``), which every user
can run. A built-in profile's ID is its slug; a custom one's is its UUID.

A profile names Shuttles, not versions: it runs whatever version is
installed. A Shuttle that is not installed (any more) is left out of a run
and reported; active Shuttles cannot be in a profile until ownership
verification exists.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nightloom_api.scans.models import ScanProfile
from nightloom_core.entities import EntityType
from nightloom_sdk.meta import Mode
from nightloom_sdk.registry import ShuttleRegistry

NAME_MAX_LENGTH = 60
DESCRIPTION_MAX_LENGTH = 300
MAX_SHUTTLES = 15
# Custom profiles an instance may hold.
MAX_CUSTOM_PROFILES = 50
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    description: str
    shuttles: tuple[str, ...]
    built_in: bool
    created_by: uuid.UUID | None = None
    updated_at: datetime | None = None


BUILT_IN_PROFILES: tuple[Profile, ...] = (
    Profile(
        id="quick-domain-recon",
        name="Quick domain recon",
        description="A domain's DNS records, its registration data and its registrable domain.",
        shuttles=("dns", "rdap", "registrable-domain"),
        built_in=True,
    ),
    Profile(
        id="deep-domain-recon",
        name="Deep domain recon",
        description=(
            "Everything about a domain from passive sources: DNS records, certificate "
            "transparency, registration data, archived URLs and its registrable domain."
        ),
        shuttles=("dns", "crtsh", "rdap", "wayback", "registrable-domain"),
        built_in=True,
    ),
    Profile(
        id="ip-network-context",
        name="IP / network context",
        description=(
            "Where an address, netblock or AS sits: reverse DNS, routing and AS data, "
            "registration data and Shodan's open ports."
        ),
        shuttles=("reverse-dns", "ripestat", "rdap", "internetdb"),
        built_in=True,
    ),
    Profile(
        id="exposure-check",
        name="Exposure check",
        description="What is open on an address (Shodan InternetDB) and which names point to it.",
        shuttles=("internetdb", "reverse-dns"),
        built_in=True,
    ),
)
_BUILT_IN = {profile.id: profile for profile in BUILT_IN_PROFILES}


class ProfileError(Exception):
    """``str(error)`` is safe to return to the client."""

    status_code = 422

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


def clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not 1 <= len(cleaned) <= NAME_MAX_LENGTH or _CONTROL.search(cleaned):
        raise ProfileError(f"name must be 1 to {NAME_MAX_LENGTH} characters of plain text.")
    return cleaned


def clean_description(description: str) -> str:
    cleaned = " ".join(description.split())
    if len(cleaned) > DESCRIPTION_MAX_LENGTH or _CONTROL.search(cleaned):
        raise ProfileError(
            f"description must be at most {DESCRIPTION_MAX_LENGTH} characters of plain text."
        )
    return cleaned


def check_shuttles(names: Sequence[str], registry: ShuttleRegistry) -> tuple[str, ...]:
    """The Shuttle names, de-duplicated in order; each installed and passive (else 422)."""
    unique = tuple(dict.fromkeys(names))
    if not 1 <= len(unique) <= MAX_SHUTTLES:
        raise ProfileError(f"A profile runs 1 to {MAX_SHUTTLES} Shuttles.")
    for name in unique:
        shuttle = registry.get(name)
        if shuttle is None:
            raise ProfileError(f"Unknown Shuttle: {name[:63]}.")
        if shuttle.meta.mode != Mode.PASSIVE:
            raise ProfileError(
                f"{name} is an active Shuttle; profiles may only hold passive Shuttles "
                "until ownership verification is available."
            )
    return unique


def accepted_types(profile: Profile, registry: ShuttleRegistry) -> list[EntityType]:
    """Entity types at least one of the profile's installed Shuttles accepts."""
    types: set[EntityType] = set()
    for name in profile.shuttles:
        shuttle = registry.get(name)
        if shuttle is not None and shuttle.meta.mode == Mode.PASSIVE:
            types |= set(shuttle.meta.input_types)
    return sorted(types)


def missing_shuttles(profile: Profile, registry: ShuttleRegistry) -> list[str]:
    return [name for name in profile.shuttles if registry.get(name) is None]


def _from_row(row: ScanProfile) -> Profile:
    return Profile(
        id=str(row.id),
        name=row.name,
        description=row.description,
        shuttles=tuple(row.shuttles),
        built_in=False,
        created_by=row.created_by,
        updated_at=row.updated_at,
    )


async def list_profiles(db: AsyncSession) -> list[Profile]:
    """Built-in profiles first, then custom ones by name."""
    rows = (await db.execute(select(ScanProfile).order_by(func.lower(ScanProfile.name)))).scalars()
    return [*BUILT_IN_PROFILES, *(_from_row(row) for row in rows)]


async def get_row(db: AsyncSession, profile_id: str, *, lock: bool = False) -> ScanProfile | None:
    try:
        parsed = uuid.UUID(profile_id)
    except ValueError:
        return None
    statement = select(ScanProfile).where(ScanProfile.id == parsed)
    if lock:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def get_profile(db: AsyncSession, profile_id: str) -> Profile | None:
    if profile_id in _BUILT_IN:
        return _BUILT_IN[profile_id]
    row = await get_row(db, profile_id)
    return _from_row(row) if row is not None else None


def is_built_in(profile_id: str) -> bool:
    return profile_id in _BUILT_IN


async def name_taken(db: AsyncSession, name: str, *, other_than: uuid.UUID | None = None) -> bool:
    """Whether a built-in or another custom profile already has ``name`` (ignoring case)."""
    lowered = name.lower()
    if any(profile.name.lower() == lowered for profile in BUILT_IN_PROFILES):
        return True
    statement = select(ScanProfile.id).where(func.lower(ScanProfile.name) == lowered)
    if other_than is not None:
        statement = statement.where(ScanProfile.id != other_than)
    return (await db.execute(statement)).first() is not None


async def count_custom(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(ScanProfile)) or 0)


def names(profiles: Iterable[Profile]) -> list[str]:
    return [profile.name for profile in profiles]


__all__ = [
    "BUILT_IN_PROFILES",
    "DESCRIPTION_MAX_LENGTH",
    "MAX_CUSTOM_PROFILES",
    "MAX_SHUTTLES",
    "NAME_MAX_LENGTH",
    "Profile",
    "ProfileError",
    "accepted_types",
    "check_shuttles",
    "clean_description",
    "clean_name",
    "count_custom",
    "get_profile",
    "get_row",
    "is_built_in",
    "list_profiles",
    "missing_shuttles",
    "name_taken",
]
