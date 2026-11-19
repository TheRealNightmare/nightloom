# nightloom-shuttle-wayback

A built-in Nightloom Shuttle (`wayback`): the **historical URLs** of a domain and its subdomains, from the Internet Archive's [Wayback Machine](https://web.archive.org). It reads the archive's [CDX server](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md), the index of everything the archive has captured. No API key is needed.

> **Usage conditions.** The data comes from the Internet Archive, a non-profit library. Keep requests modest. The Shuttle declares this as its `usage_note`, and the web UI shows it in the *Run Shuttle* panel next to the link to the [Internet Archive's terms of use](https://archive.org/about/terms).

| Input | Asks | Findings |
| --- | --- | --- |
| Domain | `GET https://web.archive.org/cdx/search/cdx`, three times (below) | Each archived page under the domain as a URL with `HOSTS` from its host (at most 2,000, most recently captured first); each subdomain hosting one of them as a Domain with `HAS_SUBDOMAIN` from the input (at most 450); on the input: `wayback_first_capture`, `wayback_last_capture` and `wayback_url_count` |

## How it works

- **Passive.** The Shuttle reads what the archive already captured. It only ever contacts `web.archive.org`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`). It never sends anything to the domain. The tests check this for every request.
- **Three queries**, each with `output=json` and `fl=timestamp,original,statuscode,mimetype`:
  1. `url=<domain>/&limit=1`: the home page's first capture (an exact match, so oldest first);
  2. `url=<domain>/&fastLatest=true&limit=-1`: the home page's latest capture (the CDX documentation's option for "the last N results", without reading every earlier one);
  3. `url=<domain>&matchType=domain&collapse=urlkey&limit=10000`: every archived address under the domain, subdomains included, one row per address. The index is sorted by address and then by time, so the row kept for an address is its *first* capture.

  Why not `matchType=domain` with `limit=1` and `limit=-1` for the first and last capture? With a domain match those return the first and last row in the index's *address* order, not in time. They also take the archive up to a minute each. The exact-match queries on the home page answer in about a second.
- **Scope.** A URL is kept only if its host, normalized with `nightloom_core`, is the input or a name under it (the [crt.sh Shuttle](../crtsh/README.md)'s rule). Lookalikes such as `evilexample.org` are dropped. Public suffixes (`co.uk`) and internal-only names are refused before anything is sent (`not_registrable`).
- **Static assets** are dropped, by extension or by the media type the archive recorded: images, stylesheets, scripts, fonts, audio and video, and archives (one list, `STATIC_ASSETS` in [`filtering.py`](src/nightloom_shuttle_wayback/filtering.py)).
- **Privacy.** Query strings and fragments are removed from every stored URL, and so are path parameters (`;jsessionid=…`). Their values can be session tokens, password-reset keys or email addresses. The names of `name=value` query parameters are kept as `query_params` (sorted, at most 30, each a plain identifier of at most 64 characters). `archive_url` is built from the stripped URL too, so no query value is ever stored.
  - **Hidden queries.** The path is percent-decoded up to twice. A `?` or `#` that only appears then (`/reset%3Ftoken=…`, `%3f`, `%23`, encoded twice: `%253F`) starts a query string or fragment like a real one: the stored URL is cut before the encoded sequence and only the parameter names are kept. A path still encoded after two rounds is dropped as *invalid*.
  - **Email addresses.** A URL whose decoded path contains `@` (`%40`, `%2540`, also inside a hidden query) usually names an email address, so it is dropped entirely.
  - **Credentials.** A URL with user information before the host (`user@`, `user:pass@`, also encoded) is dropped first (*credentials in URL*).
  - **Secrets in the path.** A URL is dropped (*possible secret in path*) when a decoded path segment looks like an AWS access key ID, a Google API key, a GitHub, Slack or Twilio token, a Stripe live key or a JSON Web Token, when the path holds a private-key marker (`-----BEGIN`), when a segment named `token`, `apikey`, `api_key`, `secret`, `password`, `access_token`, `auth` (and a few more) is followed by a value of 16 characters or more, or when a segment of 32 characters or more with letters and digits has a Shannon entropy of at least 4.0 bits per character. The patterns are one constant, `SECRET_PATTERNS`. The rules lean toward keeping pages: hex hashes of MD5, SHA-1 and SHA-256 length and hyphenated word slugs (`/blog/how-we-built-our-new-pipeline-2020`) stay, so a token hidden mid-segment or made of word-like parts can be missed, and a long camel-case name with digits inside can be dropped. A job note gives only the count: *N archived URL(s) looked like they contained secrets and were not stored.*
- **De-duplication.** Addresses that become the same URL once stripped and normalized (query-string variants of one page, `http://example.org:80/` and `http://example.org/`; `http` and `https` stay separate URLs) become one URL with the earliest `first_captured`, and `capture_count` says how many archived addresses were merged. It is not the number of snapshots: the listing returns one row per address.
- **Properties of a URL.** `first_captured`, `capture_count`, `last_status` (the HTTP status of the latest capture seen; none for revisit records), `mimetype`, `archive_url` (`https://web.archive.org/web/<timestamp>/<url>`, at the latest capture seen; the archive redirects to the nearest copy it has), `query_params`, and `last_captured` only where the archive reported the latest capture, which is the home page.
- **Properties of the input Domain.** `wayback_first_capture` and `wayback_last_capture` (the earliest and latest capture the answers show, assets included) and `wayback_url_count` (distinct URLs found after filtering, before the cap).
- **Caps.** 2,000 URLs, most recently captured first. A URL on a subdomain needs that subdomain as a Domain, so after 450 subdomains, URLs on further ones are left out. A run yields at most 4,901 findings. Notes say when a cap applied, and when the listing reached its 10,000 rows.
- **Notes give counts only.** Drops by reason (*credentials in URL*, *outside the domain*, *static assets*, *email in path*, *possible secret in path*, *invalid*), stripped addresses, malformed rows. Never a URL, and nothing about a matched secret.

## Errors and politeness

- The rate limit is **one request per second** to `web.archive.org`. A run makes three requests, plus retries.
- The CDX server is slow and sometimes unavailable. Each request may take 60 seconds to answer. **5xx**, timeouts and network errors are retried twice, after 5 and 15 seconds. **429** waits for `Retry-After` (10 seconds without one) at most twice, and never longer than 60 seconds. After that the job fails with *The data source did not answer after several tries…*. Any other status, or an answer that is not a JSON list, fails at once with `source_invalid_response`.
- An answer over 5 MB is asked again once for 2,500 addresses (with a note); if that is still too large the job fails with `result_too_large`.
- Time: the timeout is 300 seconds and no request starts after 230. Cancellation is checked before every request.

## Tests

`shuttles/wayback/tests/` answer from a fake CDX server (`httpx.MockTransport` behind the SDK's guarded client) with fictional captures of `example.org` in `tests/fixtures/`. They cover scope filtering (a lookalike domain), subdomains, static assets, query string and fragment removal, a URL with a token and an email address in its query (whose values must not appear anywhere in the findings or notes), queries hidden by percent-encoding, email addresses in paths, credentials in URLs, every secret pattern and the entropy rule with fake values only (none of which may appear in a finding, a note or a log record), de-duplication, the caps, first and last capture, 5xx, 429 and timeout handling, and malformed rows. [`test_filtering.py`](tests/test_filtering.py) tests the filter on its own. One test marked `network` asks the real archive about `example.org`. It runs only with `make test-network`, never in CI.

Passive, `network=True`, sensitivity **infrastructure**. Registered under the `nightloom.shuttles` entry point as `wayback`. Findings are rated **B2** (confidence 0.8): the archive faithfully records what it captured, but a page may differ today or be gone.

Licensed under the Apache License 2.0, like the rest of Nightloom.
