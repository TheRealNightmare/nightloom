# nightloom-shuttle-gravatar

A built-in Nightloom Shuttle (`gravatar`, *Public profile of an email address*): an email address's public [Gravatar](https://gravatar.com/) profile, through Gravatar's [REST API](https://docs.gravatar.com/api/profiles/rest-api/). No key is needed.

| Input | Asks | Findings |
| --- | --- | --- |
| Email | `GET https://api.gravatar.com/v3/profiles/<SHA-256 of the trimmed, lower-cased address>` | The profile as a SocialProfile named by its address, with `platform` Gravatar, `profile_url`, `display_name`, `job_title` and `company`, and `HAS_PROFILE` from the Email. Each account its owner verified on Gravatar and did not hide (at most 20) as a SocialProfile (`platform`: the service, `profile_url`) with `LINKED_ACCOUNT` from the Gravatar profile |

## Guardrails

- **Only a hash is sent.** Gravatar looks profiles up by the address's SHA-256 hash; the address itself never leaves Nightloom. The only host contacted is `api.gravatar.com`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`).
- **People.** An email address and its profile are a person's: the Shuttle has the `person` sensitivity, so it runs only in cases whose owner allowed person-related entities, and never in an auto-pivot.
- **Less than the profile shows.** Never kept: the photo (avatar), location, description, pronouns, pronunciation, or the links a profile lists without Gravatar verifying them. Hidden verified accounts are skipped too.
- **Ratings.** Gravatar ties a profile to an address its owner confirmed: **A2** (0.9). A verified account is **B2** (0.8).

## Errors

**404** means "no public profile" (a note). **429** waits for `Retry-After` (5 seconds without one) at most twice, never longer than 30 seconds. **5xx** or a network error is retried twice, after 2 and 5 seconds. Any other status, or an answer that is not a profile, fails with `source_invalid_response`. One request a run, one a second; results are cached for a day.

## Tests

`shuttles/gravatar/tests/` answer from a fake Gravatar with a fictional profile. They check the hash, what is kept and what never is, hidden and unverified accounts, the cap, 404, 429 and 5xx. One test marked `network` asks the real API; it runs only with `make test-network`, never in CI.

Licensed under the Apache License 2.0, like the rest of Nightloom.
