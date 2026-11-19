# Nightloom

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/TheRealNightmare/nightloom/actions/workflows/ci.yml/badge.svg)](https://github.com/TheRealNightmare/nightloom/actions/workflows/ci.yml)

**Every thread, woven into the full picture.**

Nightloom is an open-source, self-hosted OSINT tool that collects **public** information and maps it as an interactive investigation graph. Start from a domain, IP address, organization or other entity, let Nightloom pivot through public data sources, and explore how everything connects — with provenance, evidence and an audit trail for every finding.

> 🚧 **Status: early development.** Nothing below is implemented yet. This README describes what is **planned**. See the [roadmap](docs/ROADMAP.md) for progress.

## Preview

The web UI has sign-in, **investigation cases** (purpose, legal basis, members, activity) and a separate **demo graph** with fictional `example.org` data, clearly labelled "Demo data". Cases have no collected data yet: data collection (Shuttles) is the next milestone. A screenshot will be added here once the UI settles.

## Quick start (developers)

So far there is an API with authentication and user management, the local infrastructure and the web UI skeleton. With [uv](https://docs.astral.sh/uv/), [Node.js](https://nodejs.org/) 26 LTS (with [Corepack](https://github.com/nodejs/corepack) for pnpm) and `make` installed:

```sh
make install       # install the Python dependencies
make web-install   # install the web dependencies (pnpm)
make deps-up       # start PostgreSQL, Valkey and Neo4j (Docker), needed by the tests
make check-all     # lint, type-check and test everything
make api           # run the API at http://127.0.0.1:8000/api/docs
make web-dev       # in a second terminal: the UI at http://127.0.0.1:5173
```

With [Docker](https://docs.docker.com/get-started/get-docker/) and Docker Compose you can run the whole stack (web UI, API, PostgreSQL, Neo4j, Valkey and S3-compatible object storage):

```sh
cp .env.example .env   # then change the passwords
make up                # start everything (waits until healthy)
make create-admin email=you@example.org name="Your Name"   # first admin; prompts for a password
```

Then open <http://127.0.0.1:8080> and sign in with the admin you just created.

There is no public sign-up: the first admin is created from the command line, and admins add everyone else (through the API for now; the user management screens are not built yet).

See [CONTRIBUTING.md](CONTRIBUTING.md#development-setup) for the full development setup and [Running the stack](CONTRIBUTING.md#running-the-stack).

## Using Nightloom

1. **Sign in** at <http://127.0.0.1:8080> with an account an admin created for you.
2. **Create a case** (admins and analysts): *Cases → New case*. Every case needs a **purpose** (30–2,000 characters: why it exists and what question it answers) and a **legal basis** (owned assets, authorized engagement, threat intelligence, journalism, academic research, self-assessment or other), and you confirm that it follows the [ethics policy](ETHICS.md). An *authorized engagement* or *other* basis also needs a note naming the engagement or explaining the basis. Person entities are off by default; turning them on needs a written reason.
3. **Add what you already know.** On the case's *Graph* tab, editors and owners of an open case choose *Add entity*, pick a type (for example *Domain*) and type a value. Nightloom stores it in its canonical form, so `Example.ORG.` and `example.org` are the same entity. *Add relationship* connects two entities with one of the relationship types allowed for that pair. Click an entity to see its key, provenance and connections, or delete it. Person-related types (email, person, phone, …) are only available in cases that allow person entities.
4. **Run Shuttles.** Shuttles collect for you. Add a domain (say `example.org`), click it, and under *Run Shuttle* in the side panel choose **Run DNS records**. The *Jobs* panel opens and shows the job queued, then running, then succeeded (or why it failed), with what it created; you can cancel it while it runs. The graph then reloads with the domain's IP addresses, name servers, mail exchangers and CNAME aliases, highlighted for a moment, and the domain's TXT, SPF, DMARC, SOA and CAA records appear under *Properties*. The DNS Shuttle only asks public resolvers (Cloudflare and Quad9 by default, `NIGHTLOOM_DNS_RESOLVERS`), never your server's own, and refuses internal names such as `printer.local`; see [The DNS Shuttle](docs/ARCHITECTURE.md#the-dns-shuttle). **Run Certificate Transparency (crt.sh)** on the same domain adds the subdomains public TLS certificates were issued for and the most recent certificates, with `COVERS` links to the names they cover; it can take a few minutes for a large domain, and its job shows notes such as how many names it dropped (email addresses, IP addresses, names outside the domain). See [The crt.sh Shuttle](docs/ARCHITECTURE.md#the-crtsh-shuttle). **Run Registration data (RDAP)** adds the domain's registration and expiry dates, status and registrar under *Properties*, with the registrar (and an organization registrant) linked by `REGISTERED_BY`; on an IP address it adds the registered network (`WITHIN`) and the organization that `OWNS` it. It never keeps people's names, email addresses, phone numbers or postal addresses; see [The RDAP Shuttle](docs/ARCHITECTURE.md#the-rdap-shuttle). **Run Routing and ASN (RIPEstat)** on an IP address adds the announced prefix that covers it (`WITHIN`), the ASes that originate it (`ANNOUNCED_BY`) and the organizations that hold them (`OWNS`); on an ASN it adds the prefixes it announces. The holder organization is the same node as RDAP's when both name it the same way; see [The RIPEstat Shuttle](docs/ARCHITECTURE.md#the-ripestat-shuttle). **Run Open ports and exposures (Shodan InternetDB)** on a public IPv4 address adds the ports Shodan's scans found open as Services (`RUNS_ON`), the hostnames it saw as Domains (`RESOLVES_TO`, rated lower because they may be stale), and the software (CPEs), known vulnerabilities (CVE IDs) and tags under *Properties*. It reads Shodan's data and never contacts the address. InternetDB is free for non-commercial use only; the *Run Shuttle* panel shows that condition. See [The InternetDB Shuttle](docs/ARCHITECTURE.md#the-internetdb-shuttle). **Run Reverse DNS (PTR)** on a public address (or a netblock of up to 256 addresses) adds the names its PTR records give as Domains (`HAS_PTR`); a name that resolves back to the address also gets `RESOLVES_TO` and a higher rating, and the relationship's note says whether it was forward-confirmed. See [The reverse DNS Shuttle](docs/ARCHITECTURE.md#the-reverse-dns-shuttle).
5. **Narrow the view.** *Filters* (on the graph) keeps only some entities: data sources (added by hand, or each Shuttle by name), sensitivity, when they were seen (last 24 hours, 7 or 30 days, or a range of dates), minimum confidence, minimum Admiralty reliability (A–E) and credibility (1–5), *focus* (only what is one to three relationships away from the selected entity) and *hide isolated entities*; the legend shows and hides types. A relationship is drawn only when both its ends are. A line such as *Showing 120 of 1,340 entities · 3 filters active* has *Clear all*. Filters are part of the page's address, so a filtered view can be shared as a link and survives a reload. When an entity has more than 25 neighbours of one type that are connected to nothing else (say 312 archived URLs of a domain), they are drawn as one dashed group node (*312 URLs*); click it to expand it, and *Collapse* in the side panel folds it again. Search still finds entities inside a group: picking one expands its group and selects it.
6. **Export.** *Export* (on the graph) downloads the case as JSON (everything, with provenance), GraphML (for Gephi, yEd or Cytoscape) or CSV (a ZIP with `entities.csv`, `relationships.csv` and a README). Any member may export. Person-related data is **redacted by default**: people, email addresses and the other person-related types keep their type, but their values become `[redacted]` and their keys pseudonyms that still connect their relationships. Case owners can include person-related data by giving a reason, which is recorded in the case's activity. Exports are limited to 100,000 entities and to 10 per user every 10 minutes; see [Exports](docs/ARCHITECTURE.md#exports).
7. **Work in the case.** Owners add colleagues by email as owner, editor or viewer (*Members*), editors and owners see who did what (*Activity*), and owners change the purpose or legal basis (which asks for a reason) and close, archive or delete the case (*Settings*).

Only members can see a case, admins included. The rules are described in [Cases](docs/ARCHITECTURE.md#cases).

### Available Shuttles

| Shuttle | Input | What it does |
| --- | --- | --- |
| `registrable-domain` | Domain | Links a subdomain to its registrable domain (`www.shop.example.co.uk` → `example.co.uk`) using the bundled Public Suffix List; no network. |
| `dns` | Domain | Looks up the domain's public DNS records (A, AAAA, CNAME, MX, NS, TXT, SOA, CAA, DMARC) through public resolvers only. |
| `crtsh` | Domain | Finds subdomains and recent TLS certificates in Certificate Transparency logs via crt.sh (no API key); drops names outside the domain, email addresses and IP addresses. |
| `rdap` | Domain, IP address, Netblock, ASN | Looks up public registration data (RDAP) at the responsible registry, found through IANA's bootstrap registries (no API key): a domain's dates, status, registrar, organization registrant and name servers; an address's or netblock's registered network and owner; an ASN's registration and owner. Keeps only organization names, never contact details. |
| `ripestat` | IP address, Netblock, ASN | Looks up routing data in the RIPE NCC's RIPEstat (no API key): the announced prefix covering an address and its origin ASes; whether a netblock is announced, by whom, and the covering prefix; an ASN's holder and the prefixes it announces (at most 500). AS holders become Organizations. |
| `internetdb` | IP address (public IPv4) | Looks up what Shodan's scans saw on the address in Shodan's InternetDB (no API key): open ports as Services, hostnames as Domains, and CPEs, CVE IDs and tags as properties. Never contacts the address. **Free for non-commercial use only**; a paid product built on this data needs a Shodan license. |
| `reverse-dns` | IP address, Netblock (up to 256 addresses) | Looks up the PTR (reverse DNS) names of a public address, or of every address in a netblock of at most 256 addresses (IPv4 /24, IPv6 /120), through the same public resolvers as `dns`. Each name is checked against forward DNS: one that resolves back to the address gets `RESOLVES_TO` and a higher rating; one that does not is kept lower-rated with a note, since anyone controlling the reverse zone can set any name. Private addresses are skipped. |
| `wayback` | Domain | Lists the pages of the domain and its subdomains that the Internet Archive's Wayback Machine has captured (no API key): URLs with their first capture, last status and a link to the archived copy, and the subdomains that host them. Never contacts the domain. Leaves out other domains and static assets, removes query strings (keeping only parameter names) and drops addresses containing an email address. |

All eight are passive. `rdap` and `ripestat` add Organizations, so they have the `organization` sensitivity; the others are infrastructure-only. None handles person-related types. `make shuttles` lists what is installed.

## Planned features

- **Graph investigations** — entities and relationships stored in Neo4j and explored in a React + Sigma.js graph UI.
- **Shuttles** — a plugin system; each Shuttle queries one data source and turns results into entities and links.
- **Auto-pivot scans** — automatic expansion with depth and budget limits, plus a passive-only mode.
- **Timeline, change diffs and scheduled monitoring** — see what changed and when.
- **AI analyst** — bring your own API key for any provider via LiteLLM. Keys belong to each user only; local models via Ollama are supported.
- **Ariadne's Thread** — find how two entities are connected.
- **Evidence capture** — stored artifacts with cryptographic hashes.
- **Patterns** — reusable investigation playbooks.
- **Import/export** — STIX 2.1, GraphML, MISP, PDF.

## Planned tech stack

| Layer | Technology |
| --- | --- |
| API | Python 3.12, FastAPI |
| Background jobs | Celery + Valkey (Redis-compatible) |
| Graph store | Neo4j |
| Relational store | PostgreSQL (users, cases, jobs, audit log) |
| Evidence files | S3-compatible object storage (SeaweedFS by default) |
| Web UI | React, Vite, TypeScript, Sigma.js |
| Self-hosting | Docker Compose |

## Planned repository layout

```text
apps/
  api/        FastAPI backend
  worker/     Celery workers
  web/        React frontend
packages/
  sdk/        Shuttle SDK
shuttles/     Built-in Shuttles (plugins)
deploy/       Docker Compose stack
docs/         Documentation
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Ethics and acceptable use](ETHICS.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## Responsible use

Nightloom is for lawful investigation of **public** information — security research, attack-surface management, journalism and education. It must not be used for stalking, harassment, doxxing or bypassing access controls. Read [ETHICS.md](ETHICS.md) before using or contributing.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).
