# nightloom-shuttle-usernames

A built-in Nightloom Shuttle (`username-presence`, *Username on other platforms*): on which platforms an account with a username exists. Every result is a **lead**, rated **C4** (confidence 0.3): the same name on another platform is often someone else.

| Input | Asks | Findings |
| --- | --- | --- |
| Username | One request to each platform whose naming rules the name fits (below), never following redirects | Each account found as a SocialProfile named by its profile address (`platform`, `profile_url`), with `HAS_PROFILE` from the Username (C4, note *same name; may be someone else*) |

## Platforms

Each platform is asked through an endpoint that answers plainly whether an account exists (an API answering 404, `null` or `[]` for an unknown name). Sites that answer an ordinary page for any name, or hide profiles behind sign-in or bot checks, are left out, because a check that cannot tell "missing" from "blocked" produces false leads.

| Platform | Asked at | "Missing" looks like |
| --- | --- | --- |
| GitHub | `api.github.com/users/<name>` | 404 |
| GitLab | `gitlab.com/api/v4/users?username=<name>` | `[]` |
| Codeberg | `codeberg.org/api/v1/users/<name>` | 404 |
| Hacker News | `hacker-news.firebaseio.com/v0/user/<name>.json` | `null` |
| Docker Hub | `hub.docker.com/v2/users/<name>/` | 404 |
| npm | `registry.npmjs.org/-/user/org.couchdb.user:<name>` | 404 |
| PyPI | `pypi.org/user/<name>/` | 404 |
| DEV | `dev.to/api/users/by_username?url=<name>` | 404 |
| Lobsters | `lobste.rs/~<name>.json` | 404 |
| Keybase | `keybase.io/_/api/1.0/user/lookup.json?usernames=<name>` | `{"them": [null]}` |
| Chess.com | `api.chess.com/pub/player/<name>` | 404 |
| Lichess | `lichess.org/api/user/<name>` | 404 |

A name is sent to a platform only if it fits that platform's own naming rules (so `alex.dev` is not sent to GitHub, which does not allow dots), and those rules never allow `/`, `?`, `#`, `%` or `..`. Every host is a constant in [`platforms.py`](src/nightloom_shuttle_usernames/platforms.py).

## Guardrails

- **People.** The Shuttle has the `person` sensitivity: it runs only in cases whose owner allowed person-related entities, and never in an auto-pivot.
- **Existence only.** Only whether an account exists is read: never its profile, posts, followers or photo.
- **Leads, labelled.** Every link is C4, and every run with a result says in a note that the same name is not proof of the same person.
- **Unclear answers are not results.** A 403, 429, 5xx, redirect, network error or answer in an unexpected shape counts as "did not answer clearly", listed in a note. The job fails as *source unavailable* only when no platform answered clearly.

One request a platform; the rate limit is per host. Timeout 120 seconds; no request starts after 100. Results are cached for a week.

## Tests

`shuttles/usernames/tests/` answer from a fake for every platform (`httpx.MockTransport` behind the SDK's guarded client). They cover each answer kind, names that fit only some platforms, unclear answers, redirects (never followed), the all-unclear failure and that no request goes to a host outside the list. One test marked `network` asks the real platforms about `octocat`; it runs only with `make test-network`, never in CI.

Licensed under the Apache License 2.0, like the rest of Nightloom.
