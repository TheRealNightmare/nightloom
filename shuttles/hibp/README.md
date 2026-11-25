# nightloom-shuttle-hibp

Two built-in Nightloom Shuttles for **published data breaches**, from [Have I Been Pwned](https://haveibeenpwned.com/) (HIBP, [API v3](https://haveibeenpwned.com/API/v3)). They keep **breach metadata only**: never a password, a leaked record, a paste or HIBP's description text. HIBP's breach data is licensed CC BY 4.0, so credit HIBP when you share results.

| Shuttle | Input | Asks | Findings |
| --- | --- | --- | --- |
| `hibp-breaches` (passive, organization, no key) | Domain | `GET https://haveibeenpwned.com/api/v3/breaches?domain=<site>` (the public breach list) | The breaches of the site, as Breaches with `BREACH_OF` to the domain. For a subdomain, the site's registrable domain is asked for (with `HAS_SUBDOMAIN` to the input), because HIBP lists breaches by the site's domain |
| `hibp-account` (passive, person, operator's paid key) | Email | `GET https://haveibeenpwned.com/api/v3/breachedaccount/<address>?truncateResponse=false` with the `hibp-api-key` header | The breaches the address is in, as Breaches with `EXPOSED_IN` from the address, plus the breached site's Domain with `BREACH_OF` |

A Breach is named by HIBP's unique, stable name (`Adobe`). It carries `title`, `breach_date`, `added_at` (when HIBP added it), `record_count`, `data_classes` (such as "Email addresses" and "Passwords": the *kinds* of data, never the data) and HIBP's `verified`, `fabricated`, `spam_list` and `malware` flags.

## Guardrails

- **Metadata only.** HIBP's description (its own prose, with links) and the logo are never kept, and the paste endpoints are never used.
- **Sensitive breaches.** HIBP marks some breaches *sensitive*, such as adult sites, where being in one is itself harmful to reveal. `hibp-account` never keeps them; a note counts them. `hibp-breaches` does keep them, because it describes the site, not a person.
- **Retired** breaches (withdrawn by HIBP) are never kept.
- **People.** `hibp-account` takes an Email, so it has the `person` sensitivity: it runs only in cases whose owner allowed person-related entities, and never in an auto-pivot (neither Shuttle does).
- **Ratings follow HIBP's flags.** A verified breach is **A2** (confidence 0.9), an unverified one **C3** (0.5) and a fabricated one **D4** (0.3).

## The API key

Only `hibp-account` needs a key: HIBP's `breachedaccount` endpoint is for [paid subscribers](https://haveibeenpwned.com/API/Key). The operator sets it for the worker as `NIGHTLOOM_HIBP_API_KEY` in `.env`. It is read from the environment and never stored with a case, logged, or shown; a malformed key (not 32 hexadecimal characters) stops the worker at start with a clear message. Without a key the Shuttle is still listed, and its jobs fail as *source unavailable* with a note that says what to set. A key HIBP refuses (HTTP 401) fails the same way, with its own note. Use it only for addresses you have a legitimate reason to check (see ETHICS.md).

## Errors and politeness

- Rate limits: `hibp-breaches` one request every 2 seconds; `hibp-account` one every 6 seconds (HIBP's smallest subscription allows 10 a minute). One request a run.
- **404** from `breachedaccount` means "in no breach" (a note says so). **429**: waits for `Retry-After` (6 seconds without one) at most twice and never longer than 30 seconds. **5xx** or a network error: retried twice, after 2 and 5 seconds. Any other status, or an answer that is not a list, fails with `source_invalid_response`.
- Results are cached for a day. Both Shuttles only contact `haveibeenpwned.com`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`, which HIBP requires).

## Tests

`shuttles/hibp/tests/` answer from a fake HIBP (`httpx.MockTransport` behind the SDK's guarded client) with fictional breaches. They cover both Shuttles, subdomains, sensitive, retired, unverified, fabricated and malformed breaches, the key (missing, malformed, refused, and never in a note or an error), 404, 429 and 5xx, and that no request goes anywhere but `haveibeenpwned.com`. One test marked `network` asks the real public breach list about `adobe.com`; it runs only with `make test-network`, never in CI.

Licensed under the Apache License 2.0, like the rest of Nightloom.
