"""The GitHub commit authors Shuttle: who committed to a public repository lately.

It reads the latest :data:`MAX_COMMITS` commits on a GitHub repository's
default branch (``GET /repos/<owner>/<name>/commits``) and yields their
authors, which are people:

- each **GitHub account** GitHub linked to a commit as a Username (with
  ``platform`` GitHub) and ``CONTRIBUTED_TO`` the repository;
- each **commit email address** as an Email and ``CONTRIBUTED_TO`` the
  repository;
- ``USES_EMAIL`` from an account to the address of a commit GitHub linked
  to it.

At most :data:`MAX_AUTHORS` accounts and as many addresses are kept, those
with the most commits first. The relationship's note says how many of the
commits read were theirs and the date of the latest.

**People.** Commit authors are people, so the Shuttle has the ``person``
sensitivity: it runs only in cases whose owner allowed person-related
entities, never in an auto-pivot. Only what Git and GitHub publish with
every commit is read: no author names (free text a person typed), no
commit messages, no ``Co-authored-by`` lines, no diffs. Bot accounts, and
GitHub's no-reply addresses (``...@users.noreply.github.com``), are left out.

**Ratings.** GitHub links a commit to an account only when the commit's
address is a verified address of that account: an account's
``CONTRIBUTED_TO`` is **A2** (0.9) and ``USES_EMAIL`` **B2** (0.8). A commit's
address is whatever the committer's Git was set to, which anyone can set
to anything: an address's ``CONTRIBUTED_TO`` is **C3** (0.5).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import NormalizationError, normalize
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
from nightloom_shuttle_github.accounts import login_of
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

REPOSITORY = EntityType.REPOSITORY
USERNAME = EntityType.USERNAME
EMAIL = EntityType.EMAIL
CONTRIBUTED_TO = RelationType.CONTRIBUTED_TO
USES_EMAIL = RelationType.USES_EMAIL

GITHUB = "github.com"
PLATFORM = "GitHub"
TIMEOUT_SECONDS = 60
REQUEST_BUDGET_SECONDS = 45
MAX_COMMITS = 100
MAX_AUTHORS = 50
ADMIRALTY, CONFIDENCE = "A2", 0.9
LINKED_EMAIL = ("B2", 0.8)
SELF_ASSERTED = ("C3", 0.5)
NOREPLY_SUFFIX = "@users.noreply.github.com"
NOREPLY_ADDRESSES = frozenset({"noreply@github.com"})

USAGE_NOTE = (
    "Commit authors are people: this Shuttle runs only in cases that allow person-related "
    "entities. A commit's email address can be set to anything by whoever made it. "
    f"Optional {TOKEN_VARIABLE} raises GitHub's rate limit."
)
NOTE_NOT_GITHUB = "This Shuttle reads GitHub repositories only."
NOTE_UNKNOWN = "GitHub has no public repository at this address, or it has no commits."
NOTE_NO_AUTHORS = "None of the latest commits has an author this Shuttle keeps."
NOTE_BOTS = "Left out {count:,} commit(s) by bots or with GitHub's no-reply addresses."
NOTE_CAPPED = "{count:,} {what} committed lately; kept the {limit:,} with the most commits."
NOTE_SKIPPED = "Ignored {count:,} malformed commit(s) in GitHub's answer."
NOTE_RATE_LIMITED = (
    f"GitHub's rate limit is used up. Try later, or set {TOKEN_VARIABLE} for a higher limit."
)
NOTE_TOKEN_REFUSED = f"GitHub refused the token in {TOKEN_VARIABLE}."


@dataclass
class Tally:
    """How many of the commits read an author made, and when the latest was."""

    commits: int = 0
    latest: date | None = None

    def add(self, when: date | None) -> None:
        self.commits += 1
        if when is not None and (self.latest is None or when > self.latest):
            self.latest = when

    def note(self, read: int) -> str:
        latest = "" if self.latest is None else f", latest {self.latest.isoformat()}"
        return f"{self.commits:,} of the latest {read:,} commits{latest}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).date()


def _is_bot(account: dict[str, Any]) -> bool:
    login = account.get("login")
    return account.get("type") == "Bot" or (isinstance(login, str) and login.endswith("[bot]"))


def _address(value: Any) -> str | None:
    """A commit's address as an Email value, or ``None`` (no-reply, bot or invalid)."""
    if not isinstance(value, str) or "[bot]" in value:
        return None
    text = value.strip().lower()
    if text.endswith(NOREPLY_SUFFIX) or text in NOREPLY_ADDRESSES:
        return None
    try:
        return normalize(EMAIL, text).value
    except NormalizationError:
        return None


class GithubCommitsShuttle(Shuttle):
    """The authors of a GitHub repository's latest commits. See the module docstring."""

    meta = ShuttleMeta(
        name="github-commits",
        version="1.0.0",
        title="Commit authors of a repository (GitHub)",
        description=(
            "Reads the latest 100 commits of a public GitHub repository and adds their authors: "
            "the GitHub accounts as Usernames and the commit email addresses as Emails, each "
            "CONTRIBUTED_TO the repository, and USES_EMAIL where GitHub linked an address to an "
            "account. Leaves out bots and no-reply addresses; never reads names, messages or "
            "code. Person-related: runs only in cases that allow it."
        ),
        input_types=frozenset({REPOSITORY}),
        output_entity_types=frozenset({USERNAME, EMAIL}),
        output_relationship_types=frozenset({CONTRIBUTED_TO, USES_EMAIL}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.PERSON,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage="https://docs.github.com/en/rest/commits/commits",
        terms_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        usage_note=USAGE_NOTE,
        default_admiralty=ADMIRALTY,
        default_confidence=CONFIDENCE,
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
        host, owner, name = entity.value.split("/", 2)
        if host != GITHUB:
            ctx.note(NOTE_NOT_GITHUB)
            return
        client = GithubClient(
            self._http or ctx.http,
            token=self._token or load_token(),
            sleep=self._sleep,
            clock=self._clock,
            wall_clock=self._wall_clock,
            deadline=self._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        try:
            ctx.progress("Reading the repository's latest commits.")
            body = await client.get(f"repos/{owner}/{name}/commits", {"per_page": str(MAX_COMMITS)})
            if body is None:
                ctx.note(NOTE_UNKNOWN)
                return
            if not isinstance(body, list):
                raise InvalidResponseError("the commit list is not a list")
            for finding in _findings(ctx, entity.ref, body[:MAX_COMMITS]):
                yield finding
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
            ctx.progress(
                f"{client.requests} GitHub request(s), {client.retries} retried, "
                f"{client.rate_limited} rate-limited."
            )


def _findings(ctx: ShuttleContext, repository: EntityRef, commits: list[Any]) -> list[Finding]:
    accounts: dict[str, Tally] = {}
    addresses: dict[str, Tally] = {}
    links: set[tuple[str, str]] = set()
    bots = malformed = 0
    for item in commits:
        commit = _dict(_dict(item).get("commit"))
        author = _dict(commit.get("author"))
        if not commit or not author:
            malformed += 1
            continue
        account = _dict(_dict(item).get("author"))
        if _is_bot(account):
            bots += 1
            continue
        when = _day(author.get("date"))
        login = login_of(account.get("login"))
        address = _address(author.get("email"))
        if login is None and address is None:
            bots += 1
            continue
        if login is not None:
            accounts.setdefault(login, Tally()).add(when)
        if address is not None:
            addresses.setdefault(address, Tally()).add(when)
        if login is not None and address is not None:
            links.add((login, address))
    if bots:
        ctx.note(NOTE_BOTS.format(count=bots))
    if malformed:
        ctx.note(NOTE_SKIPPED.format(count=malformed))

    read = len(commits)
    kept_accounts = _top(ctx, accounts, "accounts")
    kept_addresses = _top(ctx, addresses, "email addresses")
    if not kept_accounts and not kept_addresses:
        ctx.note(NOTE_NO_AUTHORS)
    found: list[Finding] = []
    for login in kept_accounts:
        found.append(EntityFinding(type=USERNAME, value=login, properties={"platform": PLATFORM}))
        found.append(
            RelationshipFinding(
                type=CONTRIBUTED_TO,
                source=EntityRef(type=USERNAME, value=login),
                target=repository,
                note=accounts[login].note(read),
            )
        )
    for address in kept_addresses:
        found.append(
            EntityFinding(
                type=EMAIL, value=address, admiralty=SELF_ASSERTED[0], confidence=SELF_ASSERTED[1]
            )
        )
        found.append(
            RelationshipFinding(
                type=CONTRIBUTED_TO,
                source=EntityRef(type=EMAIL, value=address),
                target=repository,
                admiralty=SELF_ASSERTED[0],
                confidence=SELF_ASSERTED[1],
                note=addresses[address].note(read),
            )
        )
    for login, address in sorted(links):
        if login in kept_accounts and address in kept_addresses:
            found.append(
                RelationshipFinding(
                    type=USES_EMAIL,
                    source=EntityRef(type=USERNAME, value=login),
                    target=EntityRef(type=EMAIL, value=address),
                    admiralty=LINKED_EMAIL[0],
                    confidence=LINKED_EMAIL[1],
                    note=f"in commits to {repository.value}",
                )
            )
    return found


def _top(ctx: ShuttleContext, tallies: dict[str, Tally], what: str) -> list[str]:
    """At most :data:`MAX_AUTHORS` keys, most commits first (then by name)."""
    ordered = sorted(tallies, key=lambda key: (-tallies[key].commits, key))
    if len(ordered) > MAX_AUTHORS:
        ctx.note(NOTE_CAPPED.format(count=len(ordered), what=what, limit=MAX_AUTHORS))
    return ordered[:MAX_AUTHORS]


__all__ = [
    "LINKED_EMAIL",
    "MAX_AUTHORS",
    "MAX_COMMITS",
    "SELF_ASSERTED",
    "GithubCommitsShuttle",
]
