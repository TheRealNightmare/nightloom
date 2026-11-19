# nightloom-shuttle-rdap

A built-in Nightloom Shuttle (`rdap`): public **registration data** for a domain, an IP address, a netblock or an AS number, through [RDAP](https://about.rdap.org/) (the Registration Data Access Protocol, RFC 9082/9083), the JSON successor of WHOIS. It asks the registry responsible for the input, found in IANA's [RDAP bootstrap registries](https://data.iana.org/rdap/). No API key is needed.

| Input | Asks | Findings |
| --- | --- | --- |
| Domain | `<server>/domain/<registrable domain>` at the TLD's registry, and the registrar's RDAP server when the registry links to it | Properties on the **registrable** Domain: `registered`, `expires`, `last_changed`, `status`, `dnssec_signed`, `registrar`, `registrar_iana_id`, `rdap_server`. The registrar as an Organization with `REGISTERED_BY` (note `role: registrar`); the registrant as an Organization with `REGISTERED_BY` (`role: registrant`) only if it is an organization whose name is not redacted; each name server as a Domain with `HAS_NS` |
| IPAddress, Netblock | `<server>/ip/<address>` or `<server>/ip/<network>/<length>` at the Regional Internet Registry (RIR) | The registered network as Netblocks with `handle`, `name`, `type`, `country`, `registry` (the RIR), `registered`, `last_changed`, `parent_handle`, and `WITHIN` from the input address (or from the input netblock when it lies inside the registered network); the owning organization as an Organization with `OWNS` to each Netblock |
| ASN | `<server>/autnum/<number>` at the RIR | `handle`, `name`, `country`, `registry`, `registered`, `last_changed` on the ASN; the owning organization as an Organization with `OWNS` |

## How it works

- **Bootstrap.** The Shuttle reads `dns.json`, `ipv4.json`, `ipv6.json` or `asn.json` from `https://data.iana.org/rdap/` through `ctx.http`, and keeps each file in the worker process for 24 hours (one fetch per file even when several jobs start at once; if a refresh fails, the stale copy is used). It picks the most specific entry (longest domain suffix, longest IP prefix, smallest AS number range) and prefers an `https` server URL. An input no server is listed for (an unknown TLD, an unallocated range) fails with a fixed message (`not_covered`).
- **Registrable domain.** RDAP has records for registered domains, not their subdomains, so `www.example.org` is looked up as `example.org` (found with `nightloom_core`'s bundled Public Suffix List), the properties go on `example.org`, and the job notes that the input was a subdomain. Public suffixes (`co.uk`) and internal-only names are refused before anything is sent.
- **Registry and registrar.** For "thin" registries such as .com and .net, the registry's answer links (`rel: related`) to the registrar's RDAP server, which often knows more. The Shuttle follows **at most one** such link and merges the two answers; the **registry wins** on every field both have. If the registrar's server fails, the job keeps the registry data and says so in a note.
- **Networks.** A network is turned into CIDR blocks from the `cidr0` extension when the server sends it, else from its `startAddress`–`endAddress` range as the minimal list of CIDR blocks (`192.0.2.0`–`192.0.2.95` is `192.0.2.0/26` and `192.0.2.64/27`); at most 16, with a note when more would be needed.

## Privacy

RDAP answers name people (registrants, administrative, technical and abuse contacts), with email addresses, phone numbers and postal addresses. This is not a person Shuttle, so one function, [`privacy.extract_contacts`](src/nightloom_shuttle_rdap/privacy.py), is the only code that reads contacts, and it keeps **only an organization's display name** for the roles the Shuttle uses:

- the **registrar** (an accredited company), unless its vCard says `kind: individual`;
- the **registrant** or network owner, only if its vCard says `kind: org`.

It never keeps names of individuals, email addresses (including abuse and role mailboxes), phone numbers, postal addresses or any other vCard field, and skips every name that contains a redaction marker (`REDACTED`, `Data Protected`, `Privacy`, `Not Disclosed`, `GDPR`, `Withheld`, … — one case-insensitive list, `REDACTION_MARKERS`) or looks like an address or a number. Skipped contacts are counted by reason in a job note (*Skipped 4 contact(s): 1 not an organization, 2 other roles (not used), 1 redacted. Only organization names are kept.*), never quoted. The tests check that no email address, phone number or postal address from their fixtures appears anywhere in the findings.

## Errors and politeness

- Every request sends `Accept: application/rdap+json` and goes through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`). The rate limit is **one request per second per server**.
- **404**: the server has no such object (an unregistered domain, an unallocated range); the job succeeds with a note and no findings.
- **429**: waits for `Retry-After` (5 seconds without one) at most twice, and never longer than 30 seconds; then the job fails with *The data source did not answer after several tries…*.
- **5xx** or a network error: retried twice, after 2 and 5 seconds. Any other status, or an answer that is not an RDAP JSON object, fails at once with `source_invalid_response`.
- Time: the timeout is 120 seconds and no request starts after 100. Cancellation is checked before every request.

RDAP servers are run by registries and registrars as a public service; IANA's [RDAP FAQ](https://www.iana.org/help/rdap-faq) describes the bootstrap. Do not raise the rate limit, and use bulk-access agreements with the registries for bulk lookups.

## Tests

`shuttles/rdap/tests/` answer from a fake IANA and fake RDAP servers (`httpx.MockTransport` behind the SDK's guarded client), with fictional data only: `example.org`/`example.net`, `192.0.2.0/24`, `2001:db8::/32` and `AS64496`, in `tests/fixtures/`. They cover the bootstrap lookups and cache, the registry–registrar merge, the privacy rules (by scanning every finding for the fixtures' contact data), the CIDR conversions and the error handling. One test marked `network` asks the real RDAP servers about `example.org` and `1.1.1.1`; it runs only with `make test-network`, never in CI.

Passive, `network=True`, sensitivity **organization** (it adds Organizations; no person-related types). Registered under the `nightloom.shuttles` entry point as `rdap`. Registry data is rated **A2** (confidence 0.9): the registry is the authoritative source, but its data may be redacted or out of date. What only the registrar's answer says is rated **B2** (0.8).

Licensed under the Apache License 2.0, like the rest of Nightloom.
