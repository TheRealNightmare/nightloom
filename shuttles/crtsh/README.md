# nightloom-shuttle-crtsh

A built-in Nightloom Shuttle (`crtsh`): what public **Certificate Transparency** (CT) logs say about a domain, through [crt.sh](https://crt.sh/). Every publicly trusted TLS certificate is logged in CT, so the names certificates were issued for are a good, passive map of a domain's subdomains. No API key is needed.

For the input domain it asks crt.sh for the certificates naming the domain itself (`?q=<domain>&output=json`) and any name under it (`?q=%25.<domain>&output=json`), and adds:

| Found | Findings |
| --- | --- |
| Names in those certificates (SANs and common name) that are the input or under it | Domain and `HAS_SUBDOMAIN` from the input (one edge from the input; no parent chain). A name seen as a wildcard (`*.dev.example.org` → `dev.example.org`) says `wildcard: true` in the relationship's note. At most 2,000 subdomains, most recently certified first |
| The 50 most recent certificates (by `not_before`) | Certificate keyed by the SHA-256 fingerprint of its DER encoding, with `issuer`, `subject_cn`, `serial_number`, `not_before`, `not_after`, `crtsh_id`, `san_count`, `self_signed`; and `COVERS` to the kept names it contains (at most 900 `COVERS` in all) |

Older certificates contribute names only: crt.sh's JSON has no fingerprint, so each certificate is downloaded (`?d=<id>`) and parsed with [cryptography](https://cryptography.io/) to compute one.

## Scope and privacy

- **Only names under the input are kept.** Each name has a leading `*.` stripped, is lower-cased and normalized with `nightloom_core` (IDNs become A-labels), and is kept only if it is the input domain or ends in `.<input>`. Lookalikes (`notexample.org`, `example.org.attacker.example`) and other customers' names on shared certificates are dropped.
- **Email addresses are dropped**, wherever they appear (SANs, a common name, a certificate's issuer): they are person data, and this is an infrastructure Shuttle. **IP addresses** in SANs are dropped too.
- What was dropped is counted by reason and reported as a job note (`Dropped 7 name(s): 1 email addresses, 1 not valid domain names, 1 IP addresses, 4 outside the searched domain.`), never the names themselves. Malformed crt.sh rows are ignored and counted the same way.
- Public suffixes (`co.uk`) and internal-only names (`printer.local`, `fileserver.corp`) are refused before anything is sent.

## Large domains and an unwell crt.sh

- An answer over the SDK's 5 MB response cap is asked again once with `&exclude=expired`, and the job gets the note *Expired certificates excluded (result too large).* If it is still too large, the job fails with a fixed message suggesting a narrower input (one of the domain's subdomains).
- crt.sh often answers 502/503 or times out. A request that fails that way (or with 429, 500, 504, or a network error) is retried up to 3 times, waiting 2, 5 and 10 seconds; then the job fails with *The data source did not answer after several tries…*. A 4xx or an answer that is not JSON fails at once. A certificate download is retried once; three failed downloads in a row stop the downloads (the names are kept).
- Time: the Shuttle's timeout is 300 seconds. No search starts after 180 seconds and no download after 250, so a slow crt.sh ends in a clear error or a note (*Reached the time limit after N of 50 certificate downloads…*) instead of a timeout.
- Cancellation is checked before every request.

## Using crt.sh politely

crt.sh is a free service run by [Sectigo](https://sectigo.com/) on donated resources; it publishes no terms of use or API policy (its source code and issue tracker are at <https://github.com/crtsh>). The Shuttle asks it **at most once every 3 seconds** (the rate limit, applied by the SDK's HTTP client per host, retries and redirects included), at most 2 searches and 50 downloads per run, identifies itself with Nightloom's `User-Agent`, and goes through `ctx.http` only (SSRF guard, 5 MB cap). Do not raise these limits; for bulk or continuous CT monitoring, use your own CT log client or a commercial service.

## Tests

`shuttles/crtsh/tests/` answer from a fake crt.sh (`httpx.MockTransport` behind the SDK's guarded client): realistic crt.sh JSON with fictional `example.org` data in `tests/fixtures/`, and certificates generated in the tests with `cryptography` (never real ones). One test marked `network` asks the real crt.sh about `example.org`; it runs only with `make test-network`, never in CI.

Passive, infrastructure-only, `network=True`. Registered under the `nightloom.shuttles` entry point as `crtsh`. Findings are rated **B2** (confidence 0.8): CT logs are reliable, but a name in a certificate shows that it was certified, not that it resolves today. Certificates, downloaded and fingerprinted locally, are rated **B1** (0.95). Uses [cryptography](https://cryptography.io/) (Apache-2.0 or BSD-3-Clause).

Licensed under the Apache License 2.0, like the rest of Nightloom.
