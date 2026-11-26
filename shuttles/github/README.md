# nightloom-shuttle-github

Built-in Nightloom Shuttles for **public code on GitHub**, through the [GitHub REST API](https://docs.github.com/en/rest). It holds an organization's repositories and a repository's commit authors; exposed secrets follow (see the roadmap).

| Shuttle | Input | Asks | Findings |
| --- | --- | --- | --- |
| `github-repos` (*Repositories of an organization*) | Organization | `GET /orgs/<login>` (found by the `github_login` property, or `GET /search/users?q=<name> type:org`), then `GET /orgs/<login>/repos?type=public&sort=pushed` | On the organization: `github_login`, `github_checked_at`. The website on its profile as a Domain it `OWNS`. Its public repositories (at most 100, most recently pushed first) as Repositories with `HAS_REPOSITORY` |

| `github-commits` (*Commit authors of a repository*) | Repository (GitHub only) | `GET /repos/<owner>/<name>/commits?per_page=100` | The GitHub accounts GitHub linked to the latest 100 commits as Usernames (`platform` GitHub) and the commit email addresses as Emails, each `CONTRIBUTED_TO` the repository (the note: how many commits, the latest date), and `USES_EMAIL` from an account to an address GitHub linked to it. At most 50 of each, most commits first |

A Repository is named `github.com/<owner>/<name>` (lower case) and carries `description` (cut to 200 characters), `language`, `topics`, `license` (SPDX ID), `stars`, `forks`, `fork`, `archived`, `homepage`, `default_branch`, `created_at`, `pushed_at` and `github_checked_at`.

## Finding the account

1. **A `github_login` property** (set through the API, or by an earlier run): that account. An invalid one is ignored with a note.
2. **Otherwise a search** of GitHub's organization accounts for the name. Up to five results are read, and only those whose display name or login equals the organization's name (ignoring case and spacing) count. None is a note; several are a note listing their logins, and nothing is added. To choose, add the organization under the login you mean.

A link found through the property is rated **A2** (0.9); one found by matching names **C2** (0.6). GitHub's own data about a repository is **A2**. The profile's website is **B2** (0.8) when GitHub verified one of the organization's domains, else **C3** (0.5).

## Guardrails

- **Commit authors are people.** `github-commits` has the `person` sensitivity: it runs only in cases whose owner allowed person-related entities, and never in an auto-pivot. It reads only the account and the address published with each commit: never author names (free text), commit messages, `Co-authored-by` lines or code. Bot accounts and GitHub's no-reply addresses are left out (a note counts them). A linked account's `CONTRIBUTED_TO` is **A2** and `USES_EMAIL` **B2** (GitHub links a commit to an account only for a verified address); an address's own `CONTRIBUTED_TO` is **C3** (0.5), because anyone can put any address in a commit.

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

`shuttles/github/tests/` answer from a fake GitHub (`httpx.MockTransport` behind the SDK's guarded client) with fictional accounts and repositories. `test_github_repos.py` covers both ways of finding an account, ambiguous and missing names, private and malformed repositories, the website, the caps, the token (never in a note or an error), rate limits, 5xx and that no request goes anywhere but `api.github.com`. `test_github_commits.py` covers linked and unlinked authors, bots, no-reply addresses, the caps, non-GitHub and empty repositories and the errors. Tests marked `network` read the real `github` organization and `octocat/hello-world`; they run only with `make test-network`, never in CI.

Licensed under the Apache License 2.0, like the rest of Nightloom.
