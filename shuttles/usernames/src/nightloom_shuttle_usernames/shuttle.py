"""The username presence Shuttle: on which platforms a username exists.

It asks each platform of :data:`~nightloom_shuttle_usernames.platforms.PLATFORMS`
whose naming rules the username fits, through an endpoint that answers
plainly whether the account exists (see that module), one request per
platform, never following redirects. For each platform that has an account
with the name, it yields the account's profile as a SocialProfile named by
its address (``platform``, ``profile_url``) and ``HAS_PROFILE`` from the
Username.

**A lead, not an identity.** The same name on two platforms is often two
people. Every link is rated **C4** (confidence 0.3): the platform reliably
says an account exists, but whether it is the same person is doubtful. A
note says so on every run.

**People.** It has the ``person`` sensitivity: it runs only in cases whose
owner allowed person-related entities, never in an auto-pivot. Only
whether an account exists is read: never its profile, posts or photo.

A platform that answers unclearly (blocked, rate limited, down, a
redirect) is counted in a note; the job fails as ``source_unavailable``
only when no platform answered clearly.
"""

import time
from collections.abc import AsyncIterator, Callable

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
from nightloom_sdk.http import DestinationNotAllowedError, ResponseTooLargeError
from nightloom_shuttle_usernames.platforms import PLATFORMS, Answer, Platform

USERNAME = EntityType.USERNAME
PROFILE = EntityType.SOCIAL_PROFILE
HAS_PROFILE = RelationType.HAS_PROFILE

LEAD = ("C4", 0.3)
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
TIMEOUT_SECONDS = 120
REQUEST_BUDGET_SECONDS = 100

USAGE_NOTE = (
    "Leads only: the same username on another platform is often someone else, so every "
    "link is rated C4. Person-related: runs only in cases that allow it. Asks each platform "
    "only whether an account with the name exists."
)
NOTE_LEAD = (
    "The same name on another platform is not proof of the same person: these links are "
    "leads, rated C4."
)
NOTE_FOUND = "Found the name on {count:,} of the {asked:,} platforms asked: {platforms}."
NOTE_NOT_FOUND = "None of the {asked:,} platforms asked has an account with this name."
NOTE_UNCLEAR = "{count:,} platform(s) did not answer clearly: {platforms}."
NOTE_NOT_ASKED = "Not asked: {count:,} platform(s) whose names cannot look like this one."


class UsernamePresenceShuttle(Shuttle):
    """On which platforms a username exists. See the module docstring."""

    meta = ShuttleMeta(
        name="username-presence",
        version="1.0.0",
        title="Username on other platforms",
        description=(
            "Asks a fixed list of platforms (GitHub, GitLab, Codeberg, Hacker News, Docker Hub, "
            "npm, PyPI, DEV, Lobsters, Keybase, Chess.com, Lichess) whether an account with "
            "the username exists, and adds each one found as a SocialProfile (HAS_PROFILE), "
            "rated C4: the same name is not proof of the same person. Reads only whether the "
            "account exists. Person-related: runs only in cases that allow it."
        ),
        input_types=frozenset({USERNAME}),
        output_entity_types=frozenset({PROFILE}),
        output_relationship_types=frozenset({HAS_PROFILE}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.PERSON,
        network=True,
        # One request per platform host; the limit is per host.
        rate_limit=RateLimit(requests=1, period_seconds=2),
        timeout_seconds=TIMEOUT_SECONDS,
        cache_ttl_seconds=7 * 24 * 3600,
        pivots=False,
        homepage="https://github.com/TheRealNightmare/nightloom/tree/main/shuttles/usernames",
        usage_note=USAGE_NOTE,
        default_admiralty=LEAD[0],
        default_confidence=LEAD[1],
    )

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        platforms: tuple[Platform, ...] = PLATFORMS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``http``, ``platforms`` and ``clock`` replace the real ones in tests."""
        self._http = http
        self._platforms = platforms
        self._clock = clock

    async def ask(self, http: httpx.AsyncClient, platform: Platform, username: str) -> Answer:
        try:
            response = await http.get(
                platform.check_url(username),
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
                follow_redirects=False,
            )
        except (httpx.HTTPError, ResponseTooLargeError, DestinationNotAllowedError):
            return Answer.UNCLEAR
        return platform.read(response.status_code, response.content)

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        http = self._http or ctx.http
        username = entity.value.strip()
        asked = [platform for platform in self._platforms if platform.accepts(username)]
        not_asked = len(self._platforms) - len(asked)
        deadline = self._clock() + REQUEST_BUDGET_SECONDS
        found: list[Platform] = []
        unclear: list[str] = []
        for index, platform in enumerate(asked, start=1):
            ctx.raise_if_cancelled()
            if self._clock() >= deadline:
                unclear.append(platform.name)
                continue
            ctx.progress(f"Asking {platform.name} ({index} of {len(asked)}).")
            answer = await self.ask(http, platform, username)
            if answer is Answer.FOUND:
                found.append(platform)
            elif answer is Answer.UNCLEAR:
                unclear.append(platform.name)

        if not_asked:
            ctx.note(NOTE_NOT_ASKED.format(count=not_asked))
        if unclear:
            ctx.note(NOTE_UNCLEAR.format(count=len(unclear), platforms=", ".join(unclear)))
        if asked and len(unclear) == len(asked):
            raise ShuttleFailedError(FailureReason.SOURCE_UNAVAILABLE)
        if not found:
            ctx.note(NOTE_NOT_FOUND.format(asked=len(asked)))
            return
        ctx.note(
            NOTE_FOUND.format(
                count=len(found),
                asked=len(asked),
                platforms=", ".join(platform.name for platform in found),
            )
        )
        ctx.note(NOTE_LEAD)
        for platform in found:
            url = platform.profile_url(username)
            yield EntityFinding(
                type=PROFILE,
                value=url,
                properties={"platform": platform.name, "profile_url": url},
            )
            yield RelationshipFinding(
                type=HAS_PROFILE,
                source=entity.ref,
                target=EntityRef(type=PROFILE, value=url),
                note="same name; may be someone else",
            )


__all__ = ["LEAD", "UsernamePresenceShuttle"]
