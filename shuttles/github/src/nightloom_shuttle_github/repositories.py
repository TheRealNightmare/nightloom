"""The GitHub repositories Shuttle: an organization's public repositories.

It finds the organization's GitHub account, in order, by its
``github_login`` property, or by searching GitHub's organization accounts
for the name and keeping those whose display name or login equals it
(ignoring case and spacing). None is a note; several are a note listing
their logins (add one as the organization's ``github_login`` through the
API, or name the organization by its login) and nothing is added.

From the account it yields:

- the Organization with ``github_login`` and ``github_checked_at``;
- the website on the account's profile as a Domain (its registrable
  domain) with ``OWNS`` (note *website on its GitHub profile*);
- its public repositories, most recently pushed first (at most
  :data:`MAX_REPOSITORIES`), as Repositories with ``HAS_REPOSITORY``.

Only ``api.github.com`` is contacted, never the organization's site. The
account's email address and location are never read into findings, and a
private repository (which an operator's token might reveal) is never kept.

Provenance: GitHub's own data about a repository is **A2** (0.9). A link
found from the organization's ``github_login`` is **A2** too; one found by
matching names is **C2** (0.6). The profile's website is **B2** (0.8) when
GitHub verified one of the organization's domains, else **C3** (0.5).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import httpx

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.normalize import (
    NormalizationError,
    normalize,
    normalize_text,
    registrable_domain,
    text_key,
)
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
from nightloom_shuttle_github.accounts import (
    GithubOrganization,
    InvalidAccountError,
    login_of,
    parse_organization,
    parse_repository,
)
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

ORG = EntityType.ORGANIZATION
DOMAIN = EntityType.DOMAIN
REPOSITORY = EntityType.REPOSITORY
OWNS = RelationType.OWNS
HAS_REPOSITORY = RelationType.HAS_REPOSITORY

TIMEOUT_SECONDS = 90
REQUEST_BUDGET_SECONDS = 70
MAX_CANDIDATES = 5
MAX_REPOSITORIES = 100
ADMIRALTY, CONFIDENCE = "A2", 0.9
NAME_MATCH = ("C2", 0.6)
VERIFIED_WEBSITE = ("B2", 0.8)
UNVERIFIED_WEBSITE = ("C3", 0.5)
WEBSITE_NOTE = "website on its GitHub profile"

USAGE_NOTE = (
    f"GitHub allows 60 API requests an hour without a token; the operator can set "
    f"{TOKEN_VARIABLE} (a token with no permissions is enough) for 5,000."
)
NOTE_UNKNOWN_LOGIN = "GitHub has no organization account called {login}."
NOTE_BAD_LOGIN = "The organization's github_login property is not a GitHub login; searched by name."
NOTE_NO_MATCH = "No GitHub organization account has this name or login."
NOTE_SEVERAL = (
    "{count:,} GitHub organization accounts have this name, so nothing was added: {listed}. "
    "Name the organization by the login you mean and run this Shuttle again."
)
NOTE_CAPPED = (
    "The account has {count:,} public repositories; added the {limit:,} pushed to most recently."
)
NOTE_RATE_LIMITED = (
    f"GitHub's rate limit is used up. Try later, or set {TOKEN_VARIABLE} for a higher limit."
)
NOTE_TOKEN_REFUSED = f"GitHub refused the token in {TOKEN_VARIABLE}."
NOTE_SKIPPED = "Ignored {count:,} private, malformed or unusable item(s) in GitHub's answer."

Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GithubRepositoriesShuttle(Shuttle):
    """An organization's public GitHub repositories. See the module docstring."""

    meta = ShuttleMeta(
        name="github-repos",
        version="1.0.0",
        title="Repositories of an organization (GitHub)",
        description=(
            "Finds an organization's GitHub account (by its github_login property, or by "
            "searching GitHub for its name or login) and adds its public repositories, most "
            "recently pushed first, with their description, language, topics, license, stars "
            "and dates (HAS_REPOSITORY), and the website on its profile (OWNS). An ambiguous "
            "name adds nothing. Works without a token (60 requests an hour)."
        ),
        input_types=frozenset({ORG}),
        output_entity_types=frozenset({ORG, DOMAIN, REPOSITORY}),
        output_relationship_types=frozenset({OWNS, HAS_REPOSITORY}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage="https://docs.github.com/en/rest",
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
        now: Now = _utcnow,
    ) -> None:
        """Every argument replaces the real one in tests.

        Without ``token``, the token comes from the environment (if any).
        """
        self._http = http
        self._token = token
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock
        self._now = now

    @classmethod
    def check_configuration(cls) -> None:
        """A set token must look like one; no token is fine."""
        load_token()

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        client = GithubClient(
            self._http or ctx.http,
            token=self._token or load_token(),
            sleep=self._sleep,
            clock=self._clock,
            wall_clock=self._wall_clock,
            deadline=self._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        run = _Run(ctx, entity, client, self._now)
        try:
            async for finding in run.findings():
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
            run.report()


class _Run:
    def __init__(
        self, ctx: ShuttleContext, entity: EntityInput, client: GithubClient, now: Now
    ) -> None:
        self.ctx = ctx
        self.entity = entity
        self.client = client
        self.now = now
        self.skipped = 0

    def report(self) -> None:
        if self.skipped:
            self.ctx.note(NOTE_SKIPPED.format(count=self.skipped))
        self.ctx.progress(
            f"{self.client.requests} GitHub request(s), {self.client.retries} retried, "
            f"{self.client.rate_limited} rate-limited."
        )

    async def organization(self, login: str) -> GithubOrganization | None:
        body = await self.client.get(f"orgs/{login}")
        if body is None:
            return None
        try:
            return parse_organization(body)
        except InvalidAccountError:
            self.skipped += 1
            return None

    async def find(self) -> tuple[GithubOrganization, bool] | None:
        """The account, and whether it was found by name (not by ``github_login``)."""
        given = self.entity.properties.get("github_login")
        login = login_of(given)
        if given is not None and login is None:
            self.ctx.note(NOTE_BAD_LOGIN)
        if login is not None:
            self.ctx.progress(f"Reading the GitHub account {login}.")
            account = await self.organization(login)
            if account is None:
                self.ctx.note(NOTE_UNKNOWN_LOGIN.format(login=login))
                return None
            return account, False

        self.ctx.progress("Searching GitHub for organization accounts with this name.")
        name = normalize_text(self.entity.value).replace('"', " ")
        body = await self.client.get(
            "search/users", {"q": f"{name} type:org", "per_page": str(MAX_CANDIDATES)}
        )
        items = body.get("items") if isinstance(body, dict) else None
        if body is not None and not isinstance(items, list):
            raise InvalidResponseError("the search answer has no items")
        wanted = text_key(self.entity.value)
        matches: dict[str, GithubOrganization] = {}
        for item in (items or [])[:MAX_CANDIDATES]:
            candidate = login_of(item.get("login") if isinstance(item, dict) else None)
            if candidate is None:
                self.skipped += 1
                continue
            account = await self.organization(candidate)
            if account is None:
                continue
            named = account.name is not None and text_key(account.name) == wanted
            if named or account.login == wanted:
                matches.setdefault(account.login, account)
        if not matches:
            self.ctx.note(NOTE_NO_MATCH)
            return None
        if len(matches) > 1:
            listed = ", ".join(sorted(matches)[:MAX_CANDIDATES])
            self.ctx.note(NOTE_SEVERAL.format(count=len(matches), listed=listed))
            return None
        (account,) = matches.values()
        return account, True

    async def findings(self) -> AsyncIterator[Finding]:
        found = await self.find()
        if found is None:
            return
        account, by_name = found
        link_admiralty, link_confidence = NAME_MATCH if by_name else (None, None)
        checked = {"github_checked_at": self.now()}
        subject = self.entity.ref
        yield EntityFinding(
            type=ORG,
            value=subject.value,
            properties={"github_login": account.login, **checked},
            admiralty=link_admiralty,
            confidence=link_confidence,
        )

        website = self.website(account.website)
        if website is not None:
            admiralty, confidence = VERIFIED_WEBSITE if account.verified else UNVERIFIED_WEBSITE
            if by_name:
                admiralty, confidence = UNVERIFIED_WEBSITE
            yield EntityFinding(type=DOMAIN, value=website)
            yield RelationshipFinding(
                type=OWNS,
                source=subject,
                target=EntityRef(type=DOMAIN, value=website),
                admiralty=admiralty,
                confidence=confidence,
                note=WEBSITE_NOTE,
            )

        self.ctx.progress("Listing the account's public repositories.")
        body = await self.client.get(
            f"orgs/{account.login}/repos",
            {
                "type": "public",
                "sort": "pushed",
                "direction": "desc",
                "per_page": str(MAX_REPOSITORIES),
            },
        )
        if body is not None and not isinstance(body, list):
            raise InvalidResponseError("the repository list is not a list")
        if account.public_repos is not None and account.public_repos > MAX_REPOSITORIES:
            self.ctx.note(NOTE_CAPPED.format(count=account.public_repos, limit=MAX_REPOSITORIES))
        seen: set[str] = set()
        for item in (body or [])[:MAX_REPOSITORIES]:
            try:
                repository = parse_repository(item)
            except InvalidAccountError:
                self.skipped += 1
                continue
            if repository.value in seen:
                continue
            seen.add(repository.value)
            yield EntityFinding(
                type=REPOSITORY,
                value=repository.value,
                properties={**repository.properties, **checked},
            )
            yield RelationshipFinding(
                type=HAS_REPOSITORY,
                source=subject,
                target=EntityRef(type=REPOSITORY, value=repository.value),
                admiralty=link_admiralty,
                confidence=link_confidence,
            )

    def website(self, address: str | None) -> str | None:
        if address is None:
            return None
        try:
            host = address.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
            return registrable_domain(normalize(DOMAIN, host).value)
        except (IndexError, NormalizationError):
            self.skipped += 1
            return None


__all__ = [
    "MAX_CANDIDATES",
    "MAX_REPOSITORIES",
    "NAME_MATCH",
    "UNVERIFIED_WEBSITE",
    "VERIFIED_WEBSITE",
    "WEBSITE_NOTE",
    "GithubRepositoriesShuttle",
]
