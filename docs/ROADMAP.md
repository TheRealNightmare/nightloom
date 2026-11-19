# Roadmap

Milestones are ordered but not dated. Checked items are done; everything else is planned.

## M0 — Foundation

Complete, apart from the user management UI (users are managed through the API and `make create-admin` for now).

- [x] Repository foundation (docs, license, templates, repo hygiene)
- [x] API skeleton (FastAPI)
- [x] CI (lint, tests, DCO check)
- [x] Docker Compose infrastructure (PostgreSQL, Neo4j, Valkey, S3-compatible object storage)
- [x] Database wiring
- [x] Web skeleton (React + Vite + TypeScript)
- [x] Auth backend (sessions, CSRF, roles, user management, sign-in rate limits)
- [x] Web sign-in (login screen, session handling, password change)
- [ ] User management UI
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
- [ ] 1.7 Graph UX and export (provenance panel, layout and filtering, export)
- [ ] Release v0.1 (production Compose file with HTTPS, backups, user management UI, tagged release with published images)

## M2 — Auto-pivot

- [ ] Auto-pivot with depth, budget, type allowlist and passive-only mode
- [ ] Scan profiles
- [ ] Result cache

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
