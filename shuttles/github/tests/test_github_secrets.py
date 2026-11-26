"""The exposed secrets Shuttle and its rules: no network, no real secrets.

Every fake secret here is built at run time from pieces (``fake(...)``), so
this file never contains a string a secret scanner (GitHub's push
protection, say) would take for a real one. The tests then check that no
secret, or any part of one, ends up in a finding, a note or an error.
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from nightloom_core.entities import EntityType, Sensitivity, build_entity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    FailureReason,
    Finding,
    RelationshipFinding,
    ShuttleContext,
    ShuttleFailedError,
)
from nightloom_sdk.http import ResponseTooLargeError, create_http_client
from nightloom_sdk.ratelimit import HostRateLimiter
from nightloom_sdk.registry import ShuttleRegistry
from nightloom_sdk.testing import assert_declared, collect, entity_input
from nightloom_shuttle_github import GithubSecretsShuttle
from nightloom_shuttle_github.exposures import (
    COMMITS_WITH_TOKEN,
    COMMITS_WITHOUT_TOKEN,
    MAX_EXPOSURES,
    NOTE_CAPPED,
    NOTE_NO_PATCH,
    NOTE_NONE,
    NOTE_NOT_GITHUB,
    NOTE_READ,
    NOTE_TOO_LARGE,
    NOTE_UNKNOWN,
)
from nightloom_shuttle_github.secrets import RULES, SeenSecrets, scan_line, scan_patch

REPOSITORY = EntityType.REPOSITORY
EXPOSURE = EntityType.SECRET_EXPOSURE
META = GithubSecretsShuttle.meta
REPO = "github.com/example-org/widgets"
TOKEN = "github_pat_" + "A1b2" * 20


def fake(prefix: str, body: str) -> str:
    """A fake secret: a prefix and a body joined at run time."""
    return prefix + body


def letters(length: int, seed: str) -> str:
    """Deterministic pseudo-random letters and digits (no placeholder-looking runs)."""
    digest = ""
    counter = 0
    while len(digest) < length:
        digest += hashlib.sha256(f"{seed}{counter}".encode()).hexdigest()
        counter += 1
    mixed = "".join(c.upper() if i % 3 == 0 else c for i, c in enumerate(digest))
    return mixed[:length].replace("0123", "0q23")


AWS = fake("AKIA", letters(16, "aws").upper().replace("0123", "Q123"))
GITHUB = fake("gh" + "p_", letters(36, "gh"))
SLACK = fake("xox" + "b-", letters(30, "slack"))
STRIPE = fake("sk_" + "live_", letters(30, "stripe"))
GOOGLE = fake("AI" + "za", letters(35, "google"))
PRIVATE = fake("-----BEGIN RSA PRIV", "ATE KEY-----")
SECRETS = (AWS, GITHUB, SLACK, STRIPE, GOOGLE)


def sha(n: int) -> str:
    return f"{n:040x}"


def patch(*lines: str, start: int = 10) -> str:
    """A unified diff: a hunk starting at ``start`` with the given lines."""
    return f"@@ -{start},2 +{start},{len(lines)} @@ def config():\n" + "\n".join(lines)


SETTINGS = patch(
    " import os",
    f'+AWS_KEY = "{AWS}"',
    "-OLD = 1",
    f"+github = '{GITHUB}'  # and another: {SLACK}",
    ' DEBUG = "AKIAIOSFODNN7EXAMPLE"',
)
DETAILS = {
    sha(1): {
        "sha": sha(1),
        "commit": {"author": {"date": "2026-11-20T08:00:00Z"}},
        "files": [
            {"filename": "config/settings.py", "status": "modified", "patch": SETTINGS},
            {"filename": "logo.png", "status": "added"},  # binary: no patch
            {"filename": "old.txt", "status": "removed"},
        ],
    },
    sha(2): {
        "sha": sha(2),
        "commit": {"author": {"date": "2026-11-19T08:00:00Z"}},
        "files": [
            # The same AWS key again (older commit): reported once, in the newer one.
            {"filename": "deploy/env", "status": "added", "patch": patch(f"+KEY={AWS}", start=1)},
            {
                "filename": "keys/id rsa",
                "status": "added",
                "patch": patch(f"+{PRIVATE}", "+MIIE", start=1),
            },
            {
                "filename": "docs/README.md",
                "status": "added",
                "patch": patch("+key: AKIAXXXXXXXXXXXXXXXX", start=1),
            },
        ],
    },
}


class FakeGithub:
    def __init__(self, commits: Any = None, details: dict[str, Any] | None = None) -> None:
        self.commits = [{"sha": key} for key in DETAILS] if commits is None else commits
        self.details = DETAILS if details is None else details
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "api.github.com"
        path = request.url.path.removeprefix("/repos/example-org/widgets/commits")
        if path == "":
            if isinstance(self.commits, httpx.Response):
                return self.commits
            return httpx.Response(200, json=self.commits)
        detail = self.details.get(path.removeprefix("/"))
        if detail is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(detail, Exception):
            raise detail
        return httpx.Response(200, json=detail)


async def run_shuttle(
    fake_github: FakeGithub, value: str = REPO, *, token: str | None = None
) -> tuple[list[Finding], ShuttleContext]:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    assert META.rate_limit is not None
    limiter = HostRateLimiter(META.rate_limit, clock=lambda: clock[0], sleep=sleep)
    http = create_http_client(
        META, transport=httpx.MockTransport(fake_github.handler), limiter=limiter
    )
    ctx = ShuttleContext(META, http=http)
    entity = entity_input(REPOSITORY, value)
    shuttle = GithubSecretsShuttle(
        token=token, sleep=sleep, clock=lambda: clock[0], wall_clock=lambda: 0.0
    )
    found = await collect(shuttle, entity, ctx=ctx)
    assert_declared(META, found, entity)
    for finding in found:
        if isinstance(finding, EntityFinding):
            build_entity(finding.type, finding.value, finding.properties)
    assert_no_secret(repr(found) + repr(ctx.notes) + repr(ctx.last_progress))
    return found, ctx


def assert_no_secret(text: str) -> None:
    """No secret, and no 8-character piece of one, anywhere in ``text``."""
    for secret in (*SECRETS, PRIVATE):
        for start in range(0, len(secret) - 8):
            piece = secret[start : start + 8]
            if piece.isalnum() and not piece.isalpha():
                assert piece not in text, "part of a secret was kept"


def exposures(found: Iterable[Finding]) -> dict[str, dict[str, Any]]:
    return {
        f.value: dict(f.properties or {})
        for f in found
        if isinstance(f, EntityFinding) and f.type == EXPOSURE
    }


# --- the rules ----------------------------------------------------------------------------


def test_rules_find_each_kind_and_skip_placeholders() -> None:
    kinds = {kind for kind, _ in RULES}
    assert len(kinds) == len(RULES)
    found = {kind for text in (*SECRETS, PRIVATE) for kind, _ in scan_line(f"x = '{text}'")}
    assert found == {
        "AWS access key ID",
        "GitHub token",
        "Slack token",
        "Stripe live key",
        "Google API key",
        "Private key",
    }
    for placeholder in (
        "AKIAIOSFODNN7EXAMPLE",
        "AKIAXXXXXXXXXXXXXXXX",
        fake("gh" + "p_", "a" * 36),
        fake("sk_" + "live_", "x" * 30),
        fake("-----BEGIN ENCRYPTED PRIV", "ATE KEY-----"),  # encrypted: not reported
        "password = 'hunter2'",  # generic patterns are not used
    ):
        assert list(scan_line(placeholder)) == [], placeholder


def test_patch_line_numbers_and_deduplication() -> None:
    seen = SeenSecrets()
    matches = scan_patch(SETTINGS, seen)
    # Line 10 is context, 11 the AWS key, the removed line takes none, 12 has two secrets.
    assert [(m.kind, m.line) for m in matches] == [
        ("AWS access key ID", 11),
        ("GitHub token", 12),
        ("Slack token", 12),
    ]
    assert scan_patch(SETTINGS, seen) == []  # seen in this run
    assert scan_patch(SETTINGS, SeenSecrets())  # a new run starts over
    assert not hasattr(matches[0], "value")


# --- the Shuttle --------------------------------------------------------------------------


def test_meta_and_registration() -> None:
    assert META.name == "github-secrets"
    assert META.input_types == {REPOSITORY}
    assert META.output_entity_types == {EXPOSURE}
    assert META.output_relationship_types == {RelationType.HAS_EXPOSURE}
    assert META.sensitivity is Sensitivity.ORGANIZATION
    assert (META.pivots, META.default_admiralty, META.default_confidence) == (False, "B3", 0.6)
    assert ShuttleRegistry.discover().get("github-secrets") is GithubSecretsShuttle


async def test_exposures() -> None:
    fake_github = FakeGithub()
    found, ctx = await run_shuttle(fake_github)
    assert fake_github.requests[0].url.params["per_page"] == str(COMMITS_WITHOUT_TOKEN)
    assert len(fake_github.requests) == 3  # the list and two commits

    kept = exposures(found)
    settings = f"{REPO}@{sha(1)}/config/settings.py"
    assert set(kept) == {
        f"{settings}#L11",
        f"{settings}#L12",
        f"{REPO}@{sha(2)}/keys/id rsa#L1",
    }
    assert kept[f"{settings}#L11"] == {
        "kind": "AWS access key ID",
        "commit_url": f"https://github.com/example-org/widgets/commit/{sha(1)}",
        "file_url": f"https://github.com/example-org/widgets/blob/{sha(1)}/config/settings.py#L11",
        "committed_at": datetime(2026, 11, 20, 8, 0, tzinfo=UTC),
    }
    # Two secrets on one line are one exposure listing both kinds.
    assert kept[f"{settings}#L12"]["kind"] == "GitHub token; Slack token"
    assert kept[f"{REPO}@{sha(2)}/keys/id rsa#L1"]["file_url"].endswith("/keys/id%20rsa#L1")
    links = {
        (f.source.value, f.target.value, f.note)
        for f in found
        if isinstance(f, RelationshipFinding)
    }
    assert (REPO, f"{settings}#L11", "AWS access key ID") in links
    assert NOTE_READ.format(count=2) in ctx.notes
    assert NOTE_NO_PATCH.format(count=1) in ctx.notes


async def test_token_reads_more_commits() -> None:
    fake_github = FakeGithub()
    await run_shuttle(fake_github, token=TOKEN)
    assert fake_github.requests[0].url.params["per_page"] == str(COMMITS_WITH_TOKEN)


async def test_nothing_found_and_other_cases() -> None:
    clean = {sha(1): {"files": [{"filename": "a.py", "patch": patch("+x = 1")}]}}
    found, ctx = await run_shuttle(FakeGithub([{"sha": sha(1)}], clean))
    assert found == [] and NOTE_NONE.format(count=1) in ctx.notes

    found, ctx = await run_shuttle(FakeGithub(), "gitlab.com/example/tools")
    assert (found, ctx.notes) == ([], (NOTE_NOT_GITHUB,))
    found, ctx = await run_shuttle(FakeGithub(httpx.Response(409, json={})))
    assert (found, ctx.notes) == ([], (NOTE_UNKNOWN,))

    too_large = {sha(1): ResponseTooLargeError("too large"), sha(2): DETAILS[sha(2)]}
    found, ctx = await run_shuttle(FakeGithub(details=too_large))
    assert NOTE_TOO_LARGE.format(count=1) in ctx.notes
    assert len(exposures(found)) == 2  # the second commit's AWS key and private key


async def test_exposures_are_capped() -> None:
    lines = [
        f"+k{n} = '{fake('AKIA', letters(16, f'cap{n}').upper())}'"
        for n in range(MAX_EXPOSURES + 5)
    ]
    details = {sha(1): {"files": [{"filename": "keys.py", "patch": patch(*lines, start=1)}]}}
    found, ctx = await run_shuttle(FakeGithub([{"sha": sha(1)}], details))
    assert len(exposures(found)) == MAX_EXPOSURES
    assert NOTE_CAPPED.format(limit=MAX_EXPOSURES) in ctx.notes


async def test_bad_answers() -> None:
    meta = META
    fake_github = FakeGithub({"commits": []})
    ctx = ShuttleContext(
        meta, http=create_http_client(meta, transport=httpx.MockTransport(fake_github.handler))
    )
    with pytest.raises(ShuttleFailedError) as info:
        await collect(
            GithubSecretsShuttle(wall_clock=lambda: 0.0), entity_input(REPOSITORY, REPO), ctx=ctx
        )
    assert info.value.reason is FailureReason.SOURCE_INVALID_RESPONSE


@pytest.mark.network
async def test_real_github() -> None:
    """Scans github.com/octocat/hello-world. Only with ``make test-network``."""
    ctx = ShuttleContext(META)
    await collect(
        GithubSecretsShuttle(), entity_input(REPOSITORY, "github.com/octocat/hello-world"), ctx=ctx
    )
    assert any(note.startswith("Read the latest") for note in ctx.notes)
