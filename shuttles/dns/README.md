# nightloom-shuttle-dns

Two built-in Nightloom Shuttles that share one [resolver policy](#resolver-policy): `dns` (a domain's records) and `reverse-dns` (an address's PTR names).

## DNS records (`dns`)

What public DNS says about a domain. It asks for A, AAAA, CNAME, MX, NS, TXT, SOA and CAA records and the DMARC policy at `_dmarc.<name>`, and adds:

| Record | Findings |
| --- | --- |
| A, AAAA | IPAddress (`scope: public` or `private`) and `RESOLVES_TO` from the name that owns the addresses (the input, or the end of its CNAME chain) |
| CNAME | Domain and `ALIAS_OF`, at most 5 steps along the chain |
| MX | Domain and `HAS_MX` (preference in the note); `dns_mx` on the input; a null MX is only the property |
| NS | Domain and `HAS_NS` |
| TXT, DMARC, SOA, CAA | properties of the input Domain: `dns_txt` (at most 50 records of 1,000 characters), `dns_spf`, `dns_dmarc`, `dns_soa_*`, `dns_caa` |

Private addresses found in public DNS are recorded with `scope: private` and a note; the Shuttle never connects to any address it finds.

## Reverse DNS (`reverse-dns`)

The PTR names of an IP address, or of every address in a small netblock, each checked against forward DNS.

| Input | Findings |
| --- | --- |
| IPAddress (public) | each PTR name (at most 10, alphabetically) as a Domain with `HAS_PTR` from the address; `ptr_checked_at` on the address |
| Netblock (IPv4 /24 or smaller, IPv6 /120 or smaller) | every address is asked, in order (network and broadcast addresses too); each one with PTR names becomes an IPAddress (`scope: public`, `ptr_checked_at`) with `WITHIN` to the netblock, plus the same PTR findings |

- **Forward confirmation.** Each PTR name's A (IPv4) or AAAA (IPv6) records are asked for. If they contain the address, the `HAS_PTR` note says `forward_confirmed: true`, `RESOLVES_TO` from the name back to the address is added, and the findings are rated **B2** (0.9). Otherwise the note says `forward_confirmed: false. PTR not confirmed by forward DNS; anyone controlling the reverse zone can set any name.` and the Domain and `HAS_PTR` are rated **C3** (0.5), with no `RESOLVES_TO`. Relationships carry no typed properties, so the result is in the note, as for crt.sh's `wildcard`.
- **Skipped without a query.** An address that is not public (the SDK's `is_public_address`: RFC 1918, loopback, documentation ranges, …) gets a job note and no findings; in a netblock, such addresses are skipped and counted.
- **Refused.** A netblock larger than 256 addresses fails with *Reverse DNS sweeps are limited to 256 addresses; pick a /24 or smaller (for IPv6, a /120 or smaller). Nothing was queried.*
- **Dropped names.** PTR names that are not valid domains (wildcards, single labels, numeric top-level labels) or are internal-only (the same list as below, and `.arpa` reverse names) are dropped and counted in a note, never quoted, and never queried.
- **Failures.** NXDOMAIN and empty answers are quiet. SERVFAIL and timeouts are counted. The job fails with *source unavailable* when no PTR query got an answer, or after 10 failed PTR queries in a row.
- **Limits.** Timeout 300 s; a sweep starts no query after 240 s, and stops adding names after 1,400 (under the worker's 5,000-finding limit), each with a note. Cancellation is checked before every query. Each forward name is asked once per run.
- **Notes.** A summary note on every run: addresses queried, with PTR names, names forward-confirmed, queries failed.

## Resolver policy

Both Shuttles use the same configuration ([`config.py`](src/nightloom_shuttle_dns/config.py)), resolver client ([`client.py`](src/nightloom_shuttle_dns/client.py)) and rate limit.

- Only the resolvers in `NIGHTLOOM_DNS_RESOLVERS` are asked: comma-separated IP addresses, default `1.1.1.1,9.9.9.9`:
  - Cloudflare 1.1.1.1: <https://developers.cloudflare.com/1.1.1.1/>, privacy policy <https://developers.cloudflare.com/1.1.1.1/privacy/public-dns-resolver/>
  - Quad9 9.9.9.9: <https://quad9.net/>, privacy policy <https://quad9.net/privacy/policy/>
- The host's `/etc/resolv.conf` is never read (dnspython `configure=False`, no search list, no cache): an internal resolver could put internal records into a case.
- A resolver that is not a public address is refused by `DnsShuttle.check_configuration()`, which the worker runs at startup, so the worker does not start.
- Internal-only names are refused before any query: single labels and names under `.local`, `.localhost`, `.internal`, `.home.arpa`, `.lan`, `.corp`, `.intranet`, `.test`, `.invalid`, `.example` and `.onion`.
- Each query: 3 seconds, 2 tries (the second at the next resolver), the Shuttle's rate limit (10 per second) per resolver. NXDOMAIN and empty answers are not errors.

`NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS=true` is **for tests only**: it allows private resolvers and the `nightloom-test.example` zone (the integration tests' local CoreDNS server, `make up TEST_DNS=1`). It is refused with `NIGHTLOOM_ENV=production`.

## Tests

`shuttles/dns/tests/` use a fake resolver and never touch the network. Two tests marked `network` ask the real default resolvers, one about `example.org` and one about `1.1.1.1`'s PTR name (`one.one.one.one`, forward-confirmed); they run only with `make test-network`, never in CI.

Both are passive, infrastructure-only and `network=True`. Registered under the `nightloom.shuttles` entry point as `dns` and `reverse-dns`. Uses [dnspython](https://www.dnspython.org/) (ISC license).

Licensed under the Apache License 2.0, like the rest of Nightloom.
