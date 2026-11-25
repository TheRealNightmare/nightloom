# nightloom-shuttle-github

Built-in Nightloom Shuttles for **public code on GitHub**, through the [GitHub REST API](https://docs.github.com/en/rest). This package starts with an organization's repositories; commit authors and exposed secrets follow (see the roadmap).

| Shuttle | Input | Asks | Findings |
| --- | --- | --- | --- |
| `github-repos` (*Repositories of an organization*) | Organization | `GET /orgs/<login>` (found by the `github_login` property, or `GET /search/users?q=<name> type:org`), then `GET /orgs/<login>/repos?type=public&sort=pushed` | On the organization: `github_login`, `github_checked_at`. The website on its profile as a Domain it `OWNS`. Its public repositories (at most 100, most recently pushed first) as Repositories with `HAS_REPOSITORY` |

A Repository is named `github.com/<owner>/<name>` (lower case) and carries `description` (cut to 200 characters), `language`, `topics`, `license` (SPDX ID), `stars`, `forks`, `fork`, `archived`, `homepage`, `default_branch`, `created_at`, `pushed_at` and `github_checked_at`.

## Finding the account

1. **A `github_login` property** (set through the API, or by an earlier run): that account. An invalid one is ignored with a note.
2. **Otherwise a search** of GitHub's organization accounts for the name. Up to five results are read, and only those whose display name or login equals the organization's name (ignoring case and spacing) count. None is a note; several are a note listing their logins, and nothing is added. To choose, add the organization under the login you mean.

A link found through the property is rated **A2** (0.9); one found by matching names **C2** (0.6). GitHub's own data about a repository is **A2**. The profile's website is **B2** (0.8) when GitHub verified one of the organization's domains, else **C3** (0.5).

## Guardrails

- **Passive.** Only `api.github.com` is contacted, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`, which GitHub requires); never the organization's site.
- **Organizations only.** It reads organization accounts (`/orgs/`), never personal ones. The account's email address and location are never kept.
- **Public only.** A private repository, which a token with access could reveal, is never kept.
- Never in an auto-pivot (`pivots: false`); results are cached for a day.

## The token (optional)

GitHub allows 60 API requests an hour from one address without a token, and a run takes 2 to 7. With a token it allows 5,000. The operator can set `NIGHTLOOM_GITHUB_TOKEN` for the worker (a [fine-grained token](https://github.com/settings/personal-access-tokens) with no permissions reads public data and is the safest choice). It is read from the environment and never stored with a case, logged or shown. A malformed token stops the worker at start; one GitHub refuses (401) fails the job with a note.

## Errors and politeness

- One request a second. **404** means "no such account". A used-up rate limit (403 or 429 with `x-ratelimit-remaining: 0`) waits for the reset only if it is at most 30 seconds away; otherwise the job fails as *source unavailable* with a note suggesting the token. A secondary limit (`Retry-After`) is waited for at most twice. **5xx** or a network error is retried twice, after 2 and 5 seconds; any other status fails with `source_invalid_response`.
- Time: the timeout is 90 seconds and no request starts after 70.

## Tests

`shuttles/github/tests/` answer from a fake GitHub (`httpx.MockTransport` behind the SDK's guarded client) with fictional accounts and repositories. They cover both ways of finding an account, ambiguous and missing names, private and malformed repositories, the website, the caps, the token (never in a note or an error), rate limits, 5xx and that no request goes anywhere but `api.github.com`. One test marked `network` reads the real `github` organization account; it runs only with `make test-network`, never in CI.

Licensed under the Apache License 2.0, like the rest of Nightloom.
