# nightloom-shuttle-wikidata

A built-in Nightloom Shuttle (`wikidata`): **facts about organizations** from [Wikidata](https://www.wikidata.org/), the Wikimedia Foundation's free knowledge base (CC0), through its [query service](https://query.wikidata.org/). No API key is needed.

| Input | Asks | Findings |
| --- | --- | --- |
| Organization | Finds the item, then reads it with two SPARQL queries | On the organization: `wikidata_id`, `country`, `lei`, `industry`, `wikidata_checked_at`. Each official website's registrable domain as a Domain with `OWNS` from it (at most 20). Its parent organizations (at most 10) and subsidiaries (at most 50) as Organizations with `SUBSIDIARY_OF` |
| Domain | One SPARQL query | The organizations whose official website is the domain's home page (at most 10), each with `OWNS` to the domain |

## Finding the organization's item

A name is not unique, so the Shuttle adds data only when it finds exactly one item, in this order:

1. **A `wikidata_id` property** (`Q95`). An invalid one is ignored with a note.
2. **An item ID as the name.** If the organization's name is an item ID, that item is read. The organization is then added under its English label, with everything attached to it; the ID-named entity keeps only `wikidata_id`.
3. **An `lei` property** (for example from the GLEIF Shuttle), matched against Wikidata's LEI property (P1278).
4. **The name**: items that are organizations (instance of a subclass of *organization*, Q43229) whose English label or alias is exactly the name. None is a note; several are a note listing their item IDs, and nothing is added.

## How it works

- **Passive.** The Shuttle only ever contacts `query.wikidata.org`, through `ctx.http` (SSRF guard, 5 MB cap, Nightloom's `User-Agent`, as Wikimedia's policy asks), and never the organization or the domain. The tests check this for every request.
- **Fixed queries.** Every SPARQL query comes from a template in `queries.py`. Only an item ID (`Q` and digits), an LEI (checked), a normalized host name and an escaped string literal are ever put into one.
- **People are never kept.** Wikidata describes people too, with official websites and employers. Every query excludes items that are an instance of *human* (Q5). An item ID that turns out to be a person, or not an organization at all, adds nothing and says so in a note.
- **Properties.** `country` and `lei` are set only when the item has exactly one (an LEI must have valid check digits). `industry` holds up to three English industry names, alphabetical, separated by `; `.
- **Websites.** Each official website becomes its registrable domain (`https://www.example.org/en` → `example.org`) with `OWNS` from the organization and the note *official website, per Wikidata*. For a Domain input, the URLs looked for are `http` and `https`, with and without a trailing slash, for the domain and (for a bare domain) its `www.` name.
- **Parents and subsidiaries.** A parent is found from either side (P749 *parent organization* on the item, or P355 *has subsidiary* on the parent), and so is a subsidiary. Each becomes an Organization named by its English label with `wikidata_id`, linked with `SUBSIDIARY_OF` (note *parent organization, per Wikidata*). Items without an English label, and items with the same name as the organization, are skipped.

## Errors and politeness

- The rate limit is **one request per second**. A run makes at most four requests.
- **429**: waits for `Retry-After` (5 seconds without one) at most twice, and never longer than 30 seconds.
- **5xx** (the service answers 500 when a query times out) or a network error: retried twice, after 2 and 5 seconds, then the job fails with `source_unavailable`. Any other status, or an answer that is not SPARQL JSON results, fails with `source_invalid_response`.
- Time: the timeout is 120 seconds and no request starts after 100. Cancellation is checked before every request.
- Results are cached for a week. The Shuttle is never planned by an auto-pivot.

## Tests

`shuttles/wikidata/tests/` answer from a fake query service (`httpx.MockTransport` behind the SDK's guarded client) with fictional items. They cover every way of finding an item, ambiguous and missing names, a person's item, websites, parents and subsidiaries stated on either side, the caps, query escaping, 429 and 5xx handling, and that no request goes anywhere but `query.wikidata.org`. One test marked `network` asks the real Wikidata about Apple Inc. (Q312). It runs only with `make test-network`, never in CI.

Passive, `network=True`, sensitivity **organization**, `pivots=False`. Registered under the `nightloom.shuttles` entry point as `wikidata`. Everything is rated **C2** (confidence 0.7): Wikidata is usually well sourced for organizations, but anyone can edit it and no authority checks it.

Licensed under the Apache License 2.0, like the rest of Nightloom.
