# nightloom-shuttle-gleif

A built-in Nightloom Shuttle (`gleif`): an organization's **Legal Entity Identifier (LEI) record**, its parent companies and its direct subsidiaries, from the [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api). GLEIF (the Global Legal Entity Identifier Foundation) publishes every LEI with its reference data free of charge under CC0. No API key is needed.

| Input | Asks | Findings |
| --- | --- | --- |
| Organization | `GET https://api.gleif.org/api/v1/lei-records` (by legal name) or `.../lei-records/<lei>`, then `.../direct-parent`, `.../ultimate-parent` and `.../direct-children` | On the organization: `lei`, `legal_name`, `jurisdiction`, `legal_form`, `registered_as`, `entity_status`, `lei_status`, `country` and `gleif_checked_at`. The direct and ultimate parents as Organizations with `SUBSIDIARY_OF` from it; its direct subsidiaries (at most 50) as Organizations with `SUBSIDIARY_OF` to it |

## Finding the record

An organization's name is not unique, so the Shuttle only adds data when it finds exactly one record:

1. **An `lei` property.** If the organization has one (set through the API, or by an earlier run), that LEI is looked up. An invalid one is ignored with a note.
2. **An LEI as the name.** If the organization's name is itself an LEI (say `5493001KJTIIGC8Y1R12`), that LEI is looked up. The organization is then added under its legal name, with the record and the parents and subsidiaries attached to it; the LEI-named entity keeps only `lei`.
3. **The legal name.** Otherwise GLEIF is asked for records with that legal name, and only records whose legal or other name equals the organization's name (ignoring case and spacing) count. With none, a note suggests the full registered name. With several, nothing is added, and a note lists their LEIs and jurisdictions: add the right LEI as an organization and run the Shuttle on it.

## How it works

- **Passive.** The Shuttle reads GLEIF's register. It only ever contacts `api.gleif.org`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`), and never the organization itself. The tests check this for every request.
- **People are never kept.** An LEI can belong to a *sole proprietor*: a natural person in business on their own account. If the record found is one, nothing is kept and a note says why. Parents and subsidiaries that are sole proprietors are skipped and counted in a note.
- **Only the country of an address.** GLEIF records carry legal and headquarters addresses; the Shuttle keeps the legal address's country code and nothing else (no street, city or postal code).
- **Validation.** An LEI must be 20 letters and digits with correct ISO 7064 check digits before it is put in a URL or kept. A record without a valid LEI or legal name is skipped and counted; an optional field in an unexpected form is left out. A lookup by LEI that answers with another LEI fails the job with `source_invalid_response`.
- **Relationships.** `SUBSIDIARY_OF` goes from the subsidiary to the parent, with the note `direct parent`, `ultimate parent` or `direct and ultimate parent`. A parent or subsidiary with the same name as the organization is skipped (it would be the same entity). Parent relationships are reported by the entity itself, and GLEIF does not hold one for every entity (some report an exception instead), so a missing parent is not proof that there is none.
- **Caps.** 10 records are read when searching by name, and 50 direct subsidiaries (a note says when there are more).

## Errors and politeness

- The rate limit is **one request per second** to `api.gleif.org` (GLEIF allows 60 a minute). A run makes at most five requests.
- **404** means "no record" (an unknown LEI, or no reported parent) and is not an error.
- **429**: waits for `Retry-After` (5 seconds without one) at most twice, and never longer than 30 seconds. After that the job fails with *The data source did not answer after several tries…*.
- **5xx** or a network error: retried twice, after 2 and 5 seconds. Any other status fails at once with `source_invalid_response`.
- Time: the timeout is 60 seconds and no request starts after 45. Cancellation is checked before every request.
- Results are cached for a day. The Shuttle is never planned by an auto-pivot.

## Tests

`shuttles/gleif/tests/` answer from a fake GLEIF (`httpx.MockTransport` behind the SDK's guarded client) with fictional records in `tests/fixtures/`: Example Holdings Limited with a parent, an ultimate parent and two subsidiaries, two unrelated companies sharing a name, and a sole proprietor. They cover the three ways of finding a record, ambiguous and missing names, sole proprietors as the match and as relatives, malformed records, the caps, 429 and 5xx handling, and that no request ever goes anywhere but `api.gleif.org`. One test marked `network` asks the real GLEIF about Apple Inc.'s LEI. It runs only with `make test-network`, never in CI.

Passive, `network=True`, sensitivity **organization**, `pivots=False`. Registered under the `nightloom.shuttles` entry point as `gleif`. The record is rated **A2** (confidence 0.9): it is registered by the entity and validated by its LEI issuer. Parent relationships are rated **B2** (confidence 0.8): they are reported by the entity, with varying corroboration.

Licensed under the Apache License 2.0, like the rest of Nightloom.
