# nightloom-shuttle-dns

A built-in Nightloom Shuttle (`dns`): what public DNS says about a domain. It asks for A, AAAA, CNAME, MX, NS, TXT, SOA and CAA records and the DMARC policy at `_dmarc.<name>`, and adds:

| Record | Findings |
| --- | --- |
| A, AAAA | IPAddress (`scope: public` or `private`) and `RESOLVES_TO` from the name that owns the addresses (the input, or the end of its CNAME chain) |
| CNAME | Domain and `ALIAS_OF`, at most 5 steps along the chain |
| MX | Domain and `HAS_MX` (preference in the note); `dns_mx` on the input; a null MX is only the property |
| NS | Domain and `HAS_NS` |
| TXT, DMARC, SOA, CAA | properties of the input Domain: `dns_txt` (at most 50 records of 1,000 characters), `dns_spf`, `dns_dmarc`, `dns_soa_*`, `dns_caa` |

Private addresses found in public DNS are recorded with `scope: private` and a note; the Shuttle never connects to any address it finds.

## Resolver policy

- Only the resolvers in `NIGHTLOOM_DNS_RESOLVERS` are asked: comma-separated IP addresses, default `1.1.1.1,9.9.9.9`:
  - Cloudflare 1.1.1.1: <https://developers.cloudflare.com/1.1.1.1/>, privacy policy <https://developers.cloudflare.com/1.1.1.1/privacy/public-dns-resolver/>
  - Quad9 9.9.9.9: <https://quad9.net/>, privacy policy <https://quad9.net/privacy/policy/>
- The host's `/etc/resolv.conf` is never read (dnspython `configure=False`, no search list, no cache): an internal resolver could put internal records into a case.
- A resolver that is not a public address is refused by `DnsShuttle.check_configuration()`, which the worker runs at startup, so the worker does not start.
- Internal-only names are refused before any query: single labels and names under `.local`, `.localhost`, `.internal`, `.home.arpa`, `.lan`, `.corp`, `.intranet`, `.test`, `.invalid`, `.example` and `.onion`.
- Each query: 3 seconds, 2 tries (the second at the next resolver), the Shuttle's rate limit (10 per second) per resolver. NXDOMAIN and empty answers are not errors.

`NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS=true` is **for tests only**: it allows private resolvers and the `nightloom-test.example` zone (the integration tests' local CoreDNS server, `make up TEST_DNS=1`). It is refused with `NIGHTLOOM_ENV=production`.

## Tests

`shuttles/dns/tests/` use a fake resolver and never touch the network. One test marked `network` asks the real default resolvers about `example.org`; it runs only with `make test-network`, never in CI.

Passive, infrastructure-only, `network=True`. Registered under the `nightloom.shuttles` entry point as `dns`. Uses [dnspython](https://www.dnspython.org/) (ISC license).

Licensed under the Apache License 2.0, like the rest of Nightloom.
