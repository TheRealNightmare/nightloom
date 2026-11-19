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

## UI redesign (Loom & thread)

A new look for the web UI, in steps that each keep every screen working.

- [x] U1 Design tokens, light and dark theme, self-hosted fonts, every existing screen restyled
- [x] U2 The 2D graph in the new look (knots and threads)
- [ ] U3 2D graph interactions
- [ ] U4 Saved views
- [ ] U5 3D graph view
- [ ] U6 The 3D loom on the sign-in page, and polish

## Next: Milestone 2

## M2 — Auto-pivot

- [ ] Auto-pivot with depth, budget, type allowlist and passive-only mode
- [ ] Scan profiles
- [ ] Result cache
- [ ] Ownership verification before active Shuttles may run against a target

## M3 — Web recon and monitoring

- [ ] Web recon Shuttles
- [ ] Favicon-hash and screenshot pivots
- [ ] Typosquat detection
- [ ] Certificate Transparency monitoring

## M4 — Organizations, people, leaks, code intel

- [ ] Organization Shuttles
- [ ] Person-related Shuttles with guardrails (gated, never in auto-pivot)
- [ ] Breach/leak metadata Shuttles
- [ ] Code-intelligence Shuttles

## M5 — Time

- [ ] Timeline view
- [ ] Change diffs
- [ ] Scheduled monitoring
- [ ] Linked map and timeline views

## M6 — AI analyst

- [ ] AI analyst (bring your own key via LiteLLM, Ollama support)
- [ ] Agent mode
- [ ] Multilingual entity extraction (including Bangla)

## M7 — Evidence and interoperability

- [ ] Evidence capture with hashes
- [ ] Patterns (playbooks)
- [ ] Import/export: STIX 2.1, GraphML, MISP, PDF
- [ ] Integrations

## M8 — Community

- [ ] Collaboration
- [ ] Browser extension
- [ ] Shuttle registry
