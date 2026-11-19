# nightloom-shuttle-ripestat

A built-in Nightloom Shuttle (`ripestat`): **routing data** for an IP address, a netblock or an AS number, from the [RIPEstat Data API](https://stat.ripe.net/docs/data-api/ripestat-data-api), the RIPE NCC's public data service. Its routing data comes from [RIS](https://www.ripe.net/analyse/internet-measurements/routing-information-service-ris/), route collectors that peer with hundreds of networks and record the BGP announcements they see. No API key is needed.

| Input | Asks | Findings |
| --- | --- | --- |
| IPAddress | `network-info`, then `as-overview` for each origin AS (at most 5) | The most specific announced prefix covering the address as a Netblock (`announced: true`) with `WITHIN` from the address; its origin ASes with `ANNOUNCED_BY` from the prefix, with `as_name`, `announced`, `iana_block` and `iana_block_description`; each AS's holder as an Organization with `OWNS` |
| Netblock | `prefix-overview` | `announced`, `iana_block` and `iana_block_description` on the input; its origin ASes (`as_name`) with `ANNOUNCED_BY` and their holders as Organizations with `OWNS`. When the input is not announced itself but a less specific prefix covering it is, that prefix is added as a Netblock with `WITHIN` from the input, and carries the `ANNOUNCED_BY` edges |
| ASN | `as-overview`, then `announced-prefixes` | `as_name`, `announced`, `iana_block`, `iana_block_description` on the ASN; its holder as an Organization with `OWNS`; every prefix it originated recently (RIPEstat's default period, the last two weeks) as a Netblock with `ANNOUNCED_BY` and `bgp_first_seen` / `bgp_last_seen`, at most 500 |

## How it works

- **Requests.** Every request is `https://stat.ripe.net/data/<endpoint>/data.json?resource=<r>&sourceapp=nightloom`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`). Each answer's envelope is checked with a Pydantic model: anything but `status: "ok"` with a `data` object fails the job with `source_invalid_response`.
- **Holders.** RIPEstat names an AS's holder as `"<network name> - <organization>"`, e.g. `CLOUDFLARENET - Cloudflare, Inc.`. The Shuttle splits it on the **first** `" - "`: the left part becomes the ASN's `as_name`, the right part the Organization. Without a `" - "`, the whole text is the organization. Both parts are trimmed with inner whitespace collapsed, and skipped when empty, without any letter or digit, or redacted-looking (the SDK's `nightloom_sdk.redaction.is_redacted`, the same list the RDAP Shuttle uses); skipped names are counted in a job note, never quoted.
- **Multiple origins.** A prefix announced by several ASes at once (MOAS) gets an `ANNOUNCED_BY` edge to each, and a job note.
- **Not announced.** An address or netblock that nobody announces is a job note (*not announced in the global routing table*), not an error.
- **Large networks.** An AS with more than 500 prefixes keeps 500, in a stable order: shortest prefix first, then IPv4 before IPv6, then by address. A job note says how many there were.
- **Malformed data.** Items in an answer that do not validate (an origin that is not an AS number, a prefix that is not a network) are skipped and counted in a job note.

## Errors and politeness

- The rate limit is **two requests per second** to `stat.ripe.net`. RIPEstat's [rules of usage](https://stat.ripe.net/docs/data-api/ripestat-data-api) set no request limit, allow 8 concurrent requests per IP address, ask regular users to send a `sourceapp` (this Shuttle always sends `nightloom`) and to register with the RIPE NCC above 1,000 requests a day. The [RIPEstat Service Terms and Conditions](https://www.ripe.net/about-us/legal/ripestat-service-terms-and-conditions/) apply.
- **429**: waits for `Retry-After` (5 seconds without one) at most twice, and never longer than 30 seconds; then the job fails with *The data source did not answer after several tries…*.
- **5xx** or a network error: retried twice, after 2 and 5 seconds. Any other status (RIPEstat answers `400` with `status: "error"` for a resource it does not understand) fails at once with `source_invalid_response`.
- A failed holder lookup for one of an address's origin ASes only costs a note; the prefix and the ASes are kept.
- Time: the timeout is 120 seconds and no request starts after 100. `announced-prefixes` for a large network takes RIPEstat over 20 seconds, so that request may read for 60. Cancellation is checked before every request.

## Tests

`shuttles/ripestat/tests/` answer from a fake RIPEstat (`httpx.MockTransport` behind the SDK's guarded client) with fictional data only: `192.0.2.0/24`, `198.51.100.0/24`, `2001:db8::/32`, `AS64496` and `AS64500`, in `tests/fixtures/`. They cover announced and unannounced addresses, multiple origin ASes, a more specific netblock, the 500-prefix cap and its order, holder parsing, non-ok answers, 429 and 5xx handling, malformed items, and that `sourceapp` is always sent. One test marked `network` asks the real RIPEstat about `1.1.1.1` and `AS13335`; it runs only with `make test-network`, never in CI.

Passive, `network=True`, sensitivity **organization** (it adds Organizations; no person-related types). Registered under the `nightloom.shuttles` entry point as `ripestat`. Findings are rated **B2** (confidence 0.8): RIS is a usually reliable observer of the global routing table and what it saw is probably true, but routing changes all the time and the collectors' view is not the whole Internet.

Licensed under the Apache License 2.0, like the rest of Nightloom.
