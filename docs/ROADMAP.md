# Roadmap

Milestones are ordered but not dated. Checked items are done; everything else is planned.

## M0 — Foundation

Complete.

- [x] Repository foundation (docs, license, templates, repo hygiene)
- [x] API skeleton (FastAPI)
- [x] CI (lint, tests, DCO check)
- [x] Docker Compose infrastructure (PostgreSQL, Neo4j, Valkey, S3-compatible object storage)
- [x] Database wiring
- [x] Web skeleton (React + Vite + TypeScript)
- [x] Auth backend (sessions, CSRF, roles, user management, sign-in rate limits)
- [x] Web sign-in (login screen, session handling, password change)
- [x] User management UI
- [x] Audit log (append-only, hash-chained; sign-in and user-management events)
- [x] Cases backend (stated purpose, legal basis, membership, audit)
- [x] Cases UI
- [x] Entity schema with provenance
- [x] Case graph view (manual entities)
- [x] Shuttle SDK + Celery worker (first built-in Shuttle: `registrable-domain`, no network)
- [x] First networked Shuttle (DNS) and "Run Shuttle" in the web UI

## M1 — Infrastructure MVP

Passive infrastructure Shuttles from free public sources, then a graph UI good enough to work in.

- [x] 1.1 Certificate Transparency Shuttle (`crtsh`: subdomains and certificates via crt.sh)
- [x] 1.2 RDAP Shuttle (`rdap`: domain, IP, netblock and ASN registration data)
- [x] 1.3 ASN/BGP Shuttle via RIPEstat (`ripestat`: IP → prefix → ASN, AS holders, announced prefixes)
- [x] 1.4 Shodan InternetDB Shuttle (`internetdb`: open ports, CPEs, CVEs, tags, hostnames; no key; non-commercial use)
- [x] 1.5 Wayback Machine CDX Shuttle (`wayback`: historical URLs and subdomains; query strings stripped, only parameter names kept; no key)
- [x] 1.6 Reverse DNS Shuttle (`reverse-dns`: PTR names for an address or a netblock of up to 256 addresses, forward-confirmed; same resolver policy as `dns`)
- [x] 1.7 Graph UX and export (filters kept in the URL, collapsed groups of leaves, JSON/GraphML/CSV export with person data redacted by default)
- [x] Release v0.1 ([0.1.0](../CHANGELOG.md#010---2026-11-19))
  - [x] R1 Production deployment (`deploy/compose.prod.yaml` with HTTPS through Caddy or Cloudflare Tunnel, real client IPs through the edge, `make secrets`, `make backup` / `make restore`, small-host variant; see [DEPLOYMENT.md](DEPLOYMENT.md))
  - [x] R2 User management UI (Users page with temporary passwords shown once, forced password change at first sign-in, password reset, Audit log page with filters, details and chain verification)
  - [x] R3 Release automation (one version everywhere with `make bump-version` and a CI check, CHANGELOG, tag-triggered release workflow publishing multi-architecture images to GHCR with SBOM and provenance attestations, production pulling the images by `NIGHTLOOM_VERSION`; see [RELEASING.md](RELEASING.md))
- [x] Fix: jobs stuck in `running` (a crt.sh job outlived its timeout and its soft limit, was killed at the hard limit without a record, and the reaper only ran when the next job started). Every job now has a deadline enforced in four layers (timeout, soft limit, hard limit recorded by the worker's main process, a reaper every minute), the event loop always closes, and the *Jobs* panel marks overdue jobs. See [Deadlines](ARCHITECTURE.md#deadlines-every-job-ends).

## UI redesign (Loom & thread) — done

A new look for the web UI, in steps that each keep every screen working. All six steps are done.

- [x] U1 Design tokens, light and dark theme, self-hosted fonts, every existing screen restyled
- [x] U2 The 2D graph in the new look (knots and threads)
- [x] U3 2D graph interactions (drag and pin, saved per user and case; zoom, pan and rotation with mouse, trackpad, touch and keyboard; box selection and moving a selection; a minimap; shorter canvas labels)
- [x] U4 Saved views (named snapshots of the camera, filters, expanded groups and pins, shared with the case's members; apply with undo, links that open a view)
- [x] U5 3D graph view (React Three Fiber, lazily loaded: the same layout and pins in space, depth by first seen, type or flat, orbit / pan / zoom / fly-to with mouse, trackpad, touch and keyboard, HTML labels, a minimap, 2D fallback without WebGL; saved views remember 2D or 3D)
- [x] U6 The 3D loom on the sign-in page (lazily loaded after the form, still drawing with reduced motion, Save-Data or no WebGL), and polish: one focus ring, skeleton loading states, errors with *Try again*, page titles, a themed 404 page, app icons and a web manifest, reduced motion everywhere, contrast checked from the stylesheets, axe-core checks of every screen in both themes, README screenshots (`make screenshots`)

## M2 — Auto-pivot and active checks

Complete (0.2.0).

- [x] Auto-pivot (*Pivot from here*, where the case owner allows it: the same profile on the infrastructure it finds, up to 2 hops, 200 jobs and 500 new entities; passive only, never through people or organizations)
- [x] Scan profiles (four built in, admin-defined ones; runs on up to 25 entities, released within the per-user job limit, grouped in the Jobs panel, cancellable)
- [x] Result cache (per-Shuttle lifetimes, per case by default, replay through the case's checks, *Run fresh*, admin and owner clearing)
- [x] Ownership verification before active Shuttles may run against a target (per case, owners; DNS TXT record or a file on the website; covers subdomains and the domain's addresses; re-checked daily, revoked when the proof disappears, 30 days unless renewed)
- [x] First active Shuttles on verified targets: web server headers and TLS, and a gentle TCP check of 20 common ports
- [x] Release v0.2 ([0.2.0](../CHANGELOG.md#020---2026-11-21))

## M3 — Web recon and monitoring

Complete (0.3.0).

- [x] Web recon Shuttles: site files (robots.txt, security.txt, sitemap) and web technologies, behind a per-case *Allow website visits* switch
- [x] Favicon-hash pivot: the icon as a Favicon entity named by Shodan's hash, shared by the sites that serve it, with search links to Shodan, FOFA and Censys
- [x] Screenshot pivots: screenshots in a sandboxed browser with no network of its own, kept as evidence, with a perceptual-hash *Looks like* list
- [x] Typosquat detection: up to 500 lookalikes per domain, registered ones linked with `LOOKALIKE_OF`, never in auto-pivots
- [x] Certificate Transparency monitoring: watched domains checked hourly with crt.sh, new certificates and names as alerts on the Graph tab
- [x] Release v0.3 ([0.3.0](../CHANGELOG.md#030---2026-11-23))

## Next: M4 — Organizations, people, leaks, code intel

- [ ] Organization Shuttles
  - [x] 4.1 Legal entity records and corporate parents from GLEIF (`gleif`; `SUBSIDIARY_OF`)
  - [ ] 4.2 Wikidata: an organization's official website, country and parent, and the organizations a domain is the official website of
- [ ] Person-related Shuttles with guardrails (gated, never in auto-pivot)
- [ ] Breach/leak metadata Shuttles
- [ ] Code-intelligence Shuttles

## M5 — Time

- [ ] Timeline view
- [ ] Change diffs
- [ ] Scheduled monitoring (beyond the hourly Certificate Transparency watches of 0.3.0)
- [ ] Keyword watches for Certificate Transparency (certificates for any name containing a word)
- [ ] Linked map and timeline views

## M6 — AI analyst

- [ ] AI analyst (bring your own key via LiteLLM, Ollama support)
- [ ] Agent mode
- [ ] Multilingual entity extraction (including Bangla)

## M7 — Evidence and interoperability

- [ ] Evidence capture with hashes (0.3.0 keeps screenshots as evidence named by their SHA-256; still to come: other files, chain of custody, export)
- [ ] Patterns (playbooks)
- [ ] Import/export: STIX 2.1, GraphML, MISP, PDF
- [ ] Integrations

## M8 — Community

- [ ] Collaboration
- [ ] Browser extension
- [ ] Shuttle registry
