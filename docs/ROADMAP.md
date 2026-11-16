# Roadmap

Milestones are ordered but not dated. Checked items are done; everything else is planned.

## M0 — Foundation

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
- [ ] Cases UI
- [ ] Entity schema with provenance
- [ ] Shuttle SDK + Celery worker + first DNS Shuttle

## M1 — Infrastructure MVP

- [ ] 8–10 infrastructure Shuttles
- [ ] Graph UI (Sigma.js)
- [ ] Provenance panel

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
