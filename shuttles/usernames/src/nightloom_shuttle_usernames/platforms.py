"""The platforms the username presence Shuttle asks, and how each one answers.

Each platform is asked through an endpoint that says plainly whether an
account exists: an API answering 404 (or an empty answer) for an unknown
name. Sites that answer an ordinary page for any name, or that hide
profiles behind sign-in or bot checks, are left out: a check that cannot
tell "missing" from "blocked" produces false leads. Every host is a
constant here; a username is put in a URL only after it matches the
platform's own rules for names (:attr:`Platform.pattern`), which never
allow ``/``, ``?``, ``#``, ``%`` or ``..``.

The answer kinds:

``status``
    200 means the account exists, 404 that it does not.
``json-null``
    200 with a JSON object means it exists; 200 with ``null`` that it does not.
``json-list``
    200 with a non-empty JSON list means it exists; ``[]`` that it does not.
``keybase``
    Keybase's lookup answers ``{"them": [...]}``: a non-null first item
    means the account exists.

Anything else (403, 429, 5xx, a redirect, a network error, an answer in
another shape) means the platform did not answer clearly: the Shuttle
counts it and moves on.
"""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote


class Answer(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Platform:
    """One platform: its name, which names it allows, where to ask and where the profile is."""

    name: str
    pattern: re.Pattern[str]
    check: str
    profile: str
    kind: str

    def accepts(self, username: str) -> bool:
        return self.pattern.fullmatch(username) is not None

    def check_url(self, username: str) -> str:
        return self.check.format(name=quote(username, safe=""))

    def profile_url(self, username: str) -> str:
        return self.profile.format(name=quote(username, safe=""))

    def read(self, status: int, content: bytes) -> Answer:
        """What an answer says about the account."""
        if self.kind == "status":
            return {200: Answer.FOUND, 404: Answer.MISSING}.get(status, Answer.UNCLEAR)
        if status == 404:
            return Answer.MISSING
        if status != 200:
            return Answer.UNCLEAR
        try:
            body: Any = json.loads(content)
        except ValueError:
            return Answer.UNCLEAR
        if self.kind == "json-null":
            if body is None:
                return Answer.MISSING
            return Answer.FOUND if isinstance(body, dict) and body else Answer.UNCLEAR
        if self.kind == "json-list":
            if body == []:
                return Answer.MISSING
            return Answer.FOUND if isinstance(body, list) and body else Answer.UNCLEAR
        if self.kind == "keybase":
            them = body.get("them") if isinstance(body, dict) else None
            if isinstance(them, list) and them:
                return Answer.FOUND if isinstance(them[0], dict) else Answer.MISSING
            return Answer.UNCLEAR
        return Answer.UNCLEAR


# Letters, digits and single hyphens, not at either end (GitHub, 39 at most).
_HYPHENATED = re.compile(r"[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}")
# Letters, digits, "_", "-" and ".", starting with a letter or digit.
_DOTTED = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,38}")
_SIMPLE = re.compile(r"[A-Za-z0-9_-]{2,30}")
_LOWER = re.compile(r"[a-z0-9][a-z0-9._-]{0,213}")

PLATFORMS: tuple[Platform, ...] = (
    Platform(
        "GitHub",
        _HYPHENATED,
        "https://api.github.com/users/{name}",
        "https://github.com/{name}",
        "status",
    ),
    Platform(
        "GitLab",
        _DOTTED,
        "https://gitlab.com/api/v4/users?username={name}",
        "https://gitlab.com/{name}",
        "json-list",
    ),
    Platform(
        "Codeberg",
        _DOTTED,
        "https://codeberg.org/api/v1/users/{name}",
        "https://codeberg.org/{name}",
        "status",
    ),
    Platform(
        "Hacker News",
        re.compile(r"[A-Za-z0-9_-]{2,15}"),
        "https://hacker-news.firebaseio.com/v0/user/{name}.json",
        "https://news.ycombinator.com/user?id={name}",
        "json-null",
    ),
    Platform(
        "Docker Hub",
        re.compile(r"[a-z0-9]{4,30}"),
        "https://hub.docker.com/v2/users/{name}/",
        "https://hub.docker.com/u/{name}",
        "status",
    ),
    Platform(
        "npm",
        _LOWER,
        "https://registry.npmjs.org/-/user/org.couchdb.user:{name}",
        "https://www.npmjs.com/~{name}",
        "status",
    ),
    Platform(
        "PyPI",
        _DOTTED,
        "https://pypi.org/user/{name}/",
        "https://pypi.org/user/{name}/",
        "status",
    ),
    Platform(
        "DEV",
        _SIMPLE,
        "https://dev.to/api/users/by_username?url={name}",
        "https://dev.to/{name}",
        "status",
    ),
    Platform(
        "Lobsters",
        _SIMPLE,
        "https://lobste.rs/~{name}.json",
        "https://lobste.rs/~{name}",
        "status",
    ),
    Platform(
        "Keybase",
        re.compile(r"[A-Za-z0-9_]{2,16}"),
        "https://keybase.io/_/api/1.0/user/lookup.json?usernames={name}&fields=basics",
        "https://keybase.io/{name}",
        "keybase",
    ),
    Platform(
        "Chess.com",
        re.compile(r"[A-Za-z0-9_-]{3,25}"),
        "https://api.chess.com/pub/player/{name}",
        "https://www.chess.com/member/{name}",
        "status",
    ),
    Platform(
        "Lichess",
        re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,29}"),
        "https://lichess.org/api/user/{name}",
        "https://lichess.org/@/{name}",
        "status",
    ),
)

HOSTS = frozenset(re.sub(r"^https://([^/]+)/.*$", r"\1", platform.check) for platform in PLATFORMS)

__all__ = ["HOSTS", "PLATFORMS", "Answer", "Platform"]
