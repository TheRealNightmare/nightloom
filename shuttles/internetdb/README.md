# nightloom-shuttle-internetdb

A built-in Nightloom Shuttle (`internetdb`): **open ports and exposures** of a public IPv4 address, from [Shodan's InternetDB](https://internetdb.shodan.io/). InternetDB is a free summary of Shodan's own Internet-wide scans, updated about once a week. No API key is needed.

> **Usage conditions.** InternetDB is free for non-commercial use. You may use it at a company, but building a paid product on this data requires a Shodan license. The Shuttle declares this as its `usage_note`, and the web UI shows it in the *Run Shuttle* panel next to the terms link. See [Shodan's terms of service](https://account.shodan.io/legal/tos) and the [InternetDB page](https://internetdb.shodan.io/).

| Input | Asks | Findings |
| --- | --- | --- |
| IPAddress (public IPv4) | `GET https://internetdb.shodan.io/<ip>` | Each open port as a Service `<ip>:<port>/tcp` with `RUNS_ON` to the address (at most 200); each hostname Shodan saw as a Domain with `RESOLVES_TO` to the address (at most 100); on the address: `open_ports`, `cpes`, `vulns`, `tags` and `internetdb_checked_at` |

## How it works

- **Passive.** The Shuttle reads data Shodan already collected. It only ever contacts `internetdb.shodan.io`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`). It never sends anything to the address it looks up. The tests check this for every request.
- **Skipped without a request.** IPv6 addresses (job note *InternetDB covers IPv4 addresses only.*) and addresses that are not publicly routable (private, loopback, documentation, reserved, … as decided by the SDK's `is_public_address`), which InternetDB cannot know about anyway.
- **No information.** InternetDB answers `404` (`No information available`) for an address it has nothing on. That ends the job successfully with a note.
- **Validation.** The record is checked with a Pydantic model (`ip`, `ports`, `hostnames`, `cpes`, `vulns`, `tags`). A record for a different address, or one whose fields are not lists, fails the job with `source_invalid_response`. Within the lists, items that are malformed (a port outside 1–65535, an empty string, a CPE name that does not start with `cpe:`) are skipped and counted in a job note.
- **Vulnerabilities.** Only CVE IDs (`^CVE-\d{4}-\d{4,}$`) are kept, sorted by year and number, at most 500. Anything else is counted in a note, never quoted. Shodan infers these from the software versions it saw: they are leads to verify, not confirmed findings.
- **Hostnames.** Each name is normalized with `nightloom_core`. Invalid names and internal-looking ones (no registrable domain under the Public Suffix List, such as `printer.local`, plus `.arpa` and `.onion` names) are dropped and counted. The rest are kept in alphabetical order, at most 100. The `RESOLVES_TO` edges are rated lower than the rest and carry the note *hostname reported by Shodan; may not resolve to this address now*.
- **Properties replace older values.** Each run writes all five properties, empty lists included, so the address shows what InternetDB says now rather than a mix of old and new lists.
- **Caps.** 200 ports (the lowest), 100 hostnames, 100 CPEs, 500 CVEs and 50 tags. A note says when a cap applied. A run yields at most 601 findings.

## Errors and politeness

- The rate limit is **one request per second** to `internetdb.shodan.io`. A run makes one request.
- **429**: waits for `Retry-After` (5 seconds without one) at most twice, and never longer than 30 seconds. After that the job fails with *The data source did not answer after several tries…*.
- **5xx** or a network error: retried twice, after 2 and 5 seconds. Any other status fails at once with `source_invalid_response`.
- Time: the timeout is 60 seconds and no request starts after 45. Cancellation is checked before every request.

## Tests

`shuttles/internetdb/tests/` answer from a fake InternetDB (`httpx.MockTransport` behind the SDK's guarded client) with fictional records for the documentation addresses `192.0.2.10` and `198.51.100.7` in `tests/fixtures/`. Those addresses are not public, so the tests widen the Shuttle's address filter to the documentation ranges. They cover a full record, empty lists, 404, IPv6 and private addresses (no request made), the caps, CVE and hostname filtering, 429 and 5xx handling, and that no request ever goes anywhere but `internetdb.shodan.io`. One test marked `network` asks the real InternetDB about `1.1.1.1`. It runs only with `make test-network`, never in CI.

Passive, `network=True`, sensitivity **infrastructure**. Registered under the `nightloom.shuttles` entry point as `internetdb`. Ports, CPEs, vulnerabilities and tags are rated **B2** (confidence 0.8): Shodan's own scans are a usually reliable source and probably true, but up to a week old. Hostnames are rated **B3** (confidence 0.6): Shodan saw the name at some point, but it may no longer point to this address.

Licensed under the Apache License 2.0, like the rest of Nightloom.
