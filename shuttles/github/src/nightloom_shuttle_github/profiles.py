"""The GitHub profile Shuttle: a username's public GitHub profile.

It reads ``GET /users/<login>`` for a Username and yields the account's
public profile as a SocialProfile named by its address
(``https://github.com/<login>``), with ``platform`` GitHub, ``profile_url``,
``display_name``, ``company``, ``website``, ``created_at``, ``followers``
and ``public_repos``, and ``HAS_PROFILE`` from the Username.

**People.** A personal account is a person's: the Shuttle has the
``person`` sensitivity, so it runs only in cases whose owner allowed
person-related entities, and never in an auto-pivot. It keeps what the
profile's owner chose to show, and not all of it: never the photo, the
location, the bio, the public email address or other accounts. An
organization account is left to the repositories Shuttle (a note says so).

**Same name, same person?** A Username is only a name: the same one on
another platform may be someone else. A Username whose ``platform`` is
GitHub (the commit authors Shuttle sets it) is linked **A2** (0.9); one
without a platform is linked **C3** (0.5), since it may not be this
account's owner; one on another platform gets a note and nothing else.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    FailureReason,
    Finding,
    Mode,
    RateLimit,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleFailedError,
    ShuttleMeta,
)
from nightloom_shuttle_github.accounts import login_of, web_address
from nightloom_shuttle_github.client import (
    Clock,
    GithubClient,
    InvalidResponseError,
    RateLimitedError,
    Sleep,
    SourceUnavailableError,
    TokenRefusedError,
)
from nightloom_shuttle_github.config import TOKEN_VARIABLE, load_token

USERNAME = EntityType.USERNAME
PROFILE = EntityType.SOCIAL_PROFILE
HAS_PROFILE = RelationType.HAS_PROFILE

PLATFORM = "GitHub"
UNKNOWN_PLATFORM = ("C3", 0.5)

USAGE_NOTE = (
    "A GitHub account is a person's: this Shuttle runs only in cases that allow person-related "
    "entities, and keeps only part of what the profile shows (never the photo, location, bio "
    f"or email address). Optional {TOKEN_VARIABLE} raises GitHub's rate limit."
)
NOTE_OTHER_PLATFORM = "This username is on another platform; GitHub was not asked."
NOTE_UNKNOWN = "GitHub has no account with this name."
NOTE_ORGANIZATION = "This is a GitHub organization account; the repositories Shuttle reads those."
NOTE_SAME_NAME = (
    "The username has no platform, so this GitHub account may belong to someone else "
    "with the same name: the link is rated C3."
)
NOTE_RATE_LIMITED = (
    f"GitHub's rate limit is used up. Try later, or set {TOKEN_VARIABLE} for a higher limit."
)
NOTE_TOKEN_REFUSED = f"GitHub refused the token in {TOKEN_VARIABLE}."


def _text(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def profile_properties(body: dict[str, Any], login: str) -> dict[str, Any]:
    """The kept part of a user's profile."""
    url = f"https://github.com/{login}"
    values: dict[str, Any] = {
        "platform": PLATFORM,
        "profile_url": url,
        "display_name": _text(body.get("name")),
        "company": _text(body.get("company")),
        "website": web_address(body.get("blog")),
        "created_at": body.get("created_at") if isinstance(body.get("created_at"), str) else None,
        "followers": _count(body.get("followers")),
        "public_repos": _count(body.get("public_repos")),
    }
    return {key: value for key, value in values.items() if value is not None}


class GithubProfileShuttle(Shuttle):
    """A username's public GitHub profile. See the module docstring."""

    meta = ShuttleMeta(
        name="github-user",
        version="1.0.0",
        title="Public profile of a username (GitHub)",
        description=(
            "Reads the public GitHub profile of a username: its display name, company, website, "
            "creation date and counts, as a SocialProfile linked with HAS_PROFILE. Never keeps "
            "the photo, location, bio or email address. Person-related: runs only in cases "
            "that allow it."
        ),
        input_types=frozenset({USERNAME}),
        output_entity_types=frozenset({PROFILE}),
        output_relationship_types=frozenset({HAS_PROFILE}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.PERSON,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=60,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage="https://docs.github.com/en/rest/users/users",
        terms_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        usage_note=USAGE_NOTE,
        default_admiralty="A2",
        default_confidence=0.9,
    )

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        token: str | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Every argument replaces the real one in tests.

        Without ``token``, the token comes from the environment (if any).
        """
        self._http = http
        self._token = token
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock

    @classmethod
    def check_configuration(cls) -> None:
        """A set token must look like one; no token is fine."""
        load_token()

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        platform = entity.properties.get("platform")
        if isinstance(platform, str) and platform.strip().casefold() not in (
            "github",
            "github.com",
        ):
            ctx.note(NOTE_OTHER_PLATFORM)
            return
        login = login_of(entity.value)
        if login is None:
            ctx.note(NOTE_UNKNOWN)
            return
        client = GithubClient(
            self._http or ctx.http,
            token=self._token or load_token(),
            sleep=self._sleep,
            clock=self._clock,
            wall_clock=self._wall_clock,
            deadline=self._clock() + 45,
            before_request=ctx.raise_if_cancelled,
        )
        try:
            ctx.progress("Reading the GitHub profile.")
            body = await client.get(f"users/{login}")
            if body is None:
                ctx.note(NOTE_UNKNOWN)
                return
            if not isinstance(body, dict) or login_of(body.get("login")) != login:
                raise InvalidResponseError("not the account asked for")
            if body.get("type") == "Organization":
                ctx.note(NOTE_ORGANIZATION)
                return
            if body.get("type") != "User":
                ctx.note(NOTE_UNKNOWN)
                return
            admiralty, confidence = (None, None)
            if platform is None:
                admiralty, confidence = UNKNOWN_PLATFORM
                ctx.note(NOTE_SAME_NAME)
            url = f"https://github.com/{login}"
            yield EntityFinding(type=PROFILE, value=url, properties=profile_properties(body, login))
            yield RelationshipFinding(
                type=HAS_PROFILE,
                source=entity.ref,
                target=EntityRef(type=PROFILE, value=url),
                admiralty=admiralty,
                confidence=confidence,
            )
        except RateLimitedError as error:
            ctx.note(NOTE_RATE_LIMITED)
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        except TokenRefusedError as error:
            ctx.note(NOTE_TOKEN_REFUSED)
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        except InvalidResponseError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_INVALID_RESPONSE) from error
        except SourceUnavailableError as error:
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE) from error
        finally:
            ctx.progress(f"{client.requests} GitHub request(s).")


__all__ = ["UNKNOWN_PLATFORM", "GithubProfileShuttle", "profile_properties"]
