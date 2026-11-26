"""The GitHub exposed secrets Shuttle: likely secrets in a repository's recent commits.

It lists a public GitHub repository's latest commits (:data:`COMMITS_WITHOUT_TOKEN`,
or :data:`COMMITS_WITH_TOKEN` when the operator set a token, since each
commit is one more request), reads each commit's patches
(``GET /repos/<owner>/<name>/commits/<sha>``) and scans the **added** lines
with the precise rules of :mod:`nightloom_shuttle_github.secrets`.

Each likely secret becomes an exposed secret (a SecretExposure, located as
``<repository>@<sha>/<path>#L<line>``) with ``HAS_EXPOSURE`` from the
repository and the properties ``kind``, ``commit_url``, ``file_url`` and
``committed_at`` (several likely secrets on one line are one exposure, its
``kind`` listing them). **The secret's value is never kept**: not in an entity, a
property, a note, a log or an error, not even in part or hashed. Nothing
is ever tested or used: whether a key still works is for its owner to
find out (see ETHICS.md). The same secret in several commits is reported
once, in its most recent commit. At most :data:`MAX_EXPOSURES` are kept.

Files GitHub sends without a patch (binary or too large) and commits too
large to fetch are skipped and counted in a note; so is the history beyond
the commits read. A pattern match is likely, not certain, to be a live
secret: **B3** (confidence 0.6).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

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
from nightloom_shuttle_github.secrets import SeenSecrets, scan_patch

REPOSITORY = EntityType.REPOSITORY
EXPOSURE = EntityType.SECRET_EXPOSURE
HAS_EXPOSURE = RelationType.HAS_EXPOSURE

GITHUB = "github.com"
TIMEOUT_SECONDS = 120
REQUEST_BUDGET_SECONDS = 100
COMMITS_WITHOUT_TOKEN = 10
COMMITS_WITH_TOKEN = 30
MAX_EXPOSURES = 50
SHA_LENGTH = 40

USAGE_NOTE = (
    "Reports where a likely secret was published, never the secret, and never tests it: "
    "tell the repository's owner so they can revoke it. Without a token it reads the "
    f"latest {COMMITS_WITHOUT_TOKEN} commits ({COMMITS_WITHOUT_TOKEN + 1} of GitHub's 60 "
    f"requests an hour); with {TOKEN_VARIABLE}, the latest {COMMITS_WITH_TOKEN}."
)
NOTE_NOT_GITHUB = "This Shuttle reads GitHub repositories only."
NOTE_UNKNOWN = "GitHub has no public repository at this address, or it has no commits."
NOTE_NONE = "No likely secret in the lines the latest {count:,} commit(s) added."
NOTE_READ = "Read the latest {count:,} commit(s); older history was not scanned."
NOTE_NO_PATCH = "Skipped {count:,} file(s) GitHub sent without a patch (binary or too large)."
NOTE_TOO_LARGE = "Skipped {count:,} commit(s) too large to read."
NOTE_CAPPED = "Found more than {limit:,} likely secrets; kept the {limit:,} most recent."
NOTE_SKIPPED = "Ignored {count:,} malformed item(s) in GitHub's answers."
NOTE_RATE_LIMITED = (
    f"GitHub's rate limit is used up. Try later, or set {TOKEN_VARIABLE} for a higher limit."
)
NOTE_TOKEN_REFUSED = f"GitHub refused the token in {TOKEN_VARIABLE}."


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sha(value: Any) -> str | None:
    if isinstance(value, str) and len(value) == SHA_LENGTH:
        text = value.lower()
        if all(character in "0123456789abcdef" for character in text):
            return text
    return None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


class GithubSecretsShuttle(Shuttle):
    """Likely secrets in a GitHub repository's recent commits. See the module docstring."""

    meta = ShuttleMeta(
        name="github-secrets",
        version="1.0.0",
        title="Exposed secrets in a repository (GitHub)",
        description=(
            "Scans the lines a public GitHub repository's latest commits added (10, or 30 with a "
            "token) for likely secrets in precise formats: cloud, service and API keys, tokens "
            "and unencrypted private keys. Each becomes an exposed secret (repository, commit, "
            "file and line, with links) linked with HAS_EXPOSURE. Never keeps the secret and "
            "never tests it."
        ),
        input_types=frozenset({REPOSITORY}),
        output_entity_types=frozenset({EXPOSURE}),
        output_relationship_types=frozenset({HAS_EXPOSURE}),
        mode=Mode.PASSIVE,
        sensitivity=Sensitivity.ORGANIZATION,
        network=True,
        rate_limit=RateLimit(requests=1, period_seconds=1),
        timeout_seconds=TIMEOUT_SECONDS,
        cache_ttl_seconds=24 * 3600,
        pivots=False,
        homepage="https://docs.github.com/en/rest/commits/commits",
        terms_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        usage_note=USAGE_NOTE,
        default_admiralty="B3",
        default_confidence=0.6,
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
        token = self._token or load_token()
        client = GithubClient(
            self._http or ctx.http,
            token=token,
            sleep=self._sleep,
            clock=self._clock,
            wall_clock=self._wall_clock,
            deadline=self._clock() + REQUEST_BUDGET_SECONDS,
            before_request=ctx.raise_if_cancelled,
        )
        scan = _Scan(ctx, entity.ref, f"{owner}/{name}")
        limit = COMMITS_WITH_TOKEN if token else COMMITS_WITHOUT_TOKEN
        try:
            ctx.progress("Listing the repository's latest commits.")
            body = await client.get(f"repos/{owner}/{name}/commits", {"per_page": str(limit)})
            if body is None:
                ctx.note(NOTE_UNKNOWN)
                return
            if not isinstance(body, list):
                raise InvalidResponseError("the commit list is not a list")
            shas = [sha for sha in (_sha(_dict(item).get("sha")) for item in body[:limit]) if sha]
            scan.malformed += min(len(body), limit) - len(shas)
            for index, sha in enumerate(shas, start=1):
                ctx.progress(f"Scanning commit {index} of {len(shas)}.")
                try:
                    detail = await client.get(f"repos/{owner}/{name}/commits/{sha}")
                except InvalidResponseError:
                    scan.too_large += 1  # a commit too large for the response cap
                    continue
                for finding in scan.commit(sha, detail):
                    yield finding
                if scan.full:
                    break
            scan.report(len(shas))
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


class _Scan:
    """One run's scanning state: what was seen, kept and skipped."""

    def __init__(self, ctx: ShuttleContext, repository: EntityRef, full_name: str) -> None:
        self.ctx = ctx
        self.repository = repository
        self.web = f"https://github.com/{full_name}"
        self.seen = SeenSecrets()
        self.kept = 0
        self.full = False
        self.no_patch = 0
        self.too_large = 0
        self.malformed = 0

    def commit(self, sha: str, detail: Any) -> list[Finding]:
        data = _dict(detail)
        files = data.get("files")
        if not isinstance(files, list):
            self.malformed += 1
            return []
        committed_at = _moment(_dict(_dict(data.get("commit")).get("author")).get("date"))
        found: list[Finding] = []
        for item in files:
            file = _dict(item)
            path = file.get("filename")
            patch = file.get("patch")
            if not isinstance(path, str) or not path:
                self.malformed += 1
                continue
            if not isinstance(patch, str):
                if file.get("status") != "removed":
                    self.no_patch += 1
                continue
            by_line: dict[int, list[str]] = {}
            for match in scan_patch(patch, self.seen):
                kinds = by_line.setdefault(match.line, [])
                if match.kind not in kinds:
                    kinds.append(match.kind)
            for line, kinds in by_line.items():
                if self.kept >= MAX_EXPOSURES:
                    self.full = True
                    return found
                exposure = self.exposure(sha, path, line, "; ".join(kinds), committed_at)
                if exposure is not None:
                    found.extend(exposure)
                    self.kept += 1
        return found

    def exposure(
        self, sha: str, path: str, line: int, kind: str, committed_at: datetime | None
    ) -> list[Finding] | None:
        try:
            value = normalize(EXPOSURE, f"{self.repository.value}@{sha}/{path}#L{line}").value
        except NormalizationError:
            self.malformed += 1
            return None
        properties: dict[str, Any] = {
            "kind": kind,
            "commit_url": f"{self.web}/commit/{sha}",
            "file_url": f"{self.web}/blob/{sha}/{quote(path)}#L{line}",
        }
        if committed_at is not None:
            properties["committed_at"] = committed_at
        return [
            EntityFinding(type=EXPOSURE, value=value, properties=properties),
            RelationshipFinding(
                type=HAS_EXPOSURE,
                source=self.repository,
                target=EntityRef(type=EXPOSURE, value=value),
                note=kind,
            ),
        ]

    def report(self, commits: int) -> None:
        if self.full:
            self.ctx.note(NOTE_CAPPED.format(limit=MAX_EXPOSURES))
        elif self.kept == 0:
            self.ctx.note(NOTE_NONE.format(count=commits))
        self.ctx.note(NOTE_READ.format(count=commits))
        if self.no_patch:
            self.ctx.note(NOTE_NO_PATCH.format(count=self.no_patch))
        if self.too_large:
            self.ctx.note(NOTE_TOO_LARGE.format(count=self.too_large))
        if self.malformed:
            self.ctx.note(NOTE_SKIPPED.format(count=self.malformed))


__all__ = [
    "COMMITS_WITHOUT_TOKEN",
    "COMMITS_WITH_TOKEN",
    "MAX_EXPOSURES",
    "GithubSecretsShuttle",
]
