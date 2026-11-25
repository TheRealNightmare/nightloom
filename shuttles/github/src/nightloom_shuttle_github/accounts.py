"""Reading GitHub organization accounts and repositories into what the Shuttles keep.

``GET /orgs/<login>`` answers (shortened)::

    {"login": "example-org", "id": 1, "type": "Organization",
     "name": "Example Org", "blog": "https://example.org", "is_verified": true,
     "public_repos": 42, "email": "...", "location": "...", "description": "..."}

``GET /orgs/<login>/repos`` answers a list of (shortened)::

    {"full_name": "example-org/widgets", "private": false, "visibility": "public",
     "html_url": "https://github.com/example-org/widgets",
     "description": "...", "fork": false, "archived": false,
     "language": "Python", "topics": ["osint"], "license": {"spdx_id": "Apache-2.0"},
     "stargazers_count": 12, "forks_count": 3, "homepage": "https://...",
     "default_branch": "main", "created_at": "...", "pushed_at": "..."}

Kept from an account: its login, name, website (``blog``) and whether
GitHub verified a domain of it. Never its email address or location. Kept
from a repository: what :class:`~nightloom_core.entities.RepositoryProperties`
holds. A private repository (which a token might reveal) is never kept.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from nightloom_core.entities import EntityType
from nightloom_core.normalize import NormalizationError, normalize, normalize_text

LOGIN = re.compile(r"^[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}$")
MAX_DESCRIPTION = 200
MAX_TOPICS = 20
SPDX_UNKNOWN = frozenset({"NOASSERTION", "OTHER"})


class InvalidAccountError(ValueError):
    """An item is not a usable GitHub account or repository."""


@dataclass(frozen=True)
class GithubOrganization:
    """The parts of an organization account the Shuttles keep."""

    login: str
    name: str | None = None
    website: str | None = None
    verified: bool = False
    public_repos: int | None = None


@dataclass(frozen=True)
class GithubRepository:
    """A public repository: its Repository value and properties."""

    value: str
    properties: dict[str, Any]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        text = normalize_text(value)
    except NormalizationError:
        return None
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def web_address(value: Any) -> str | None:
    """``value`` as an http(s) address with a host (``example.org`` gets ``https://``)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or len(text) > 2048:
        return None
    return text


def login_of(value: Any) -> str | None:
    """``value`` as a GitHub login (lower case), or ``None``."""
    if not isinstance(value, str) or not LOGIN.fullmatch(value.strip()):
        return None
    return value.strip().lower()


def parse_organization(item: Any) -> GithubOrganization:
    data = _dict(item)
    login = login_of(data.get("login"))
    if login is None or data.get("type", "Organization") != "Organization":
        raise InvalidAccountError("not an organization account")
    return GithubOrganization(
        login=login,
        name=_text(data.get("name")),
        website=web_address(data.get("blog")),
        verified=data.get("is_verified") is True,
        public_repos=_count(data.get("public_repos")),
    )


def parse_repository(item: Any) -> GithubRepository:
    data = _dict(item)
    if data.get("private") is not False or data.get("visibility", "public") != "public":
        raise InvalidAccountError("not a public repository")
    full_name = data.get("full_name")
    if not isinstance(full_name, str):
        raise InvalidAccountError("no repository name")
    try:
        value = normalize(EntityType.REPOSITORY, f"github.com/{full_name}").value
    except NormalizationError as error:
        raise InvalidAccountError("not a repository name") from error
    license_id = _text(_dict(data.get("license")).get("spdx_id"), 60)
    topics = [
        topic
        for topic in (_text(item, 50) for item in data.get("topics") or [] if isinstance(item, str))
        if topic is not None
    ][:MAX_TOPICS]
    values: dict[str, Any] = {
        "description": _text(data.get("description"), MAX_DESCRIPTION),
        "language": _text(data.get("language"), 60),
        "topics": topics or None,
        "license": None if license_id in SPDX_UNKNOWN else license_id,
        "stars": _count(data.get("stargazers_count")),
        "forks": _count(data.get("forks_count")),
        "fork": _flag(data.get("fork")),
        "archived": _flag(data.get("archived")),
        "homepage": web_address(data.get("homepage")),
        "default_branch": _text(data.get("default_branch"), 100),
        "created_at": _moment(data.get("created_at")),
        "pushed_at": _moment(data.get("pushed_at")),
    }
    return GithubRepository(value, {key: v for key, v in values.items() if v is not None})


__all__ = [
    "GithubOrganization",
    "GithubRepository",
    "InvalidAccountError",
    "login_of",
    "parse_organization",
    "parse_repository",
    "web_address",
]
