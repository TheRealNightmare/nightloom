# Changelog

All notable changes to Nightloom are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
How releases are cut is described in [docs/RELEASING.md](docs/RELEASING.md).

## [Unreleased]

### Added

- Auto-pivot: *Pivot from here* runs a profile on infrastructure entities, then again on the infrastructure it finds (never people or organizations, never person-related Shuttles), up to 2 hops, 200 jobs and 500 new entities per pivot. Only in cases whose owner turned on *Allow auto-pivot* in *Settings* (`allow_auto_pivot`, off by default); `pivot: true` on `POST /cases/{id}/scan-runs`, refused with `403` elsewhere. The Jobs panel shows a pivot's new entities, hop and the limit it reached (migration `0011_auto_pivot`).
- Scan profiles: run several Shuttles on one entity or a selection of up to 25 at once. Four built-in profiles (*Quick domain recon*, *Deep domain recon*, *IP / network context*, *Exposure check*) and profiles admins add on the new *Profiles* page (`/scan-profiles`, migration `0010_scan_profiles`). A run plans every (Shuttle, entity) pair the guardrails allow (at most 100 jobs, two runs in progress per user) and queues its jobs only as the requester's job slots free up, so the per-user limit holds (`/cases/{id}/scan-runs`; the worker queues the next jobs after each one, and every minute). The Jobs panel shows each run with its progress, *Cancel run* and its jobs; jobs report `run_id`. Audited as `scan_profile.*`, `scan_run.create` and `scan_run.cancel`.
- A result cache for passive Shuttles: running a Shuttle again on the same entity reuses its recent results instead of asking the source again. Shuttles declare how long (`cache_ttl_seconds`: DNS and reverse DNS 1 hour; crt.sh, RIPEstat and InternetDB 24 hours; RDAP and Wayback 7 days; never for active Shuttles), and the key includes the Shuttle's version. Cached findings are replayed through the same checks as a live run, so the case's person gate applies again, and provenance keeps when the source was asked. Per case by default (`NIGHTLOOM_RESULT_CACHE_SCOPE=case`; `instance` or `off`), with a size cap and a purge every 10 minutes (migration `0009_result_cache`).
- *Use recent results* before a run, and *From cache · fetched …* with *Run fresh* in the Jobs panel (`fresh` on `POST /cases/{id}/jobs`; jobs report `use_cache`, `from_cache` and `cached_fetched_at`, Shuttles `cache_ttl_seconds`).
- An admin *Cache* page (`GET`/`DELETE /admin/result-cache`) and *Settings → Cached Shuttle results* for case owners (`DELETE /cases/{id}/result-cache`), audited as `result_cache.clear` with counts only.
- 2D graph interactions: drag an entity to pin it where you drop it (or a whole selection), saved per user and case (`GET`/`PUT /cases/{id}/layout`, migration `0007_graph_layouts`); unpin from the side panel, a node menu or *View → Unpin all*.
- Zoom toward the pointer, trackpad two-finger panning (with a *Scroll wheel* setting), rotation (Alt or right-drag, ⟲ / ⟳, twist), Ctrl/⌘+click and Shift+drag selection, a minimap, and full keyboard control with announcements and a shortcuts dialog.
- Shorter labels on the canvas: certificates show their subject's common name or a short fingerprint, long values are cut in the middle.
- Saved views: named snapshots of a case graph's camera, filters, expanded groups and pins, shared with the case's members (`/cases/{id}/views`, migration `0008_saved_views`, audited as `view.create` / `view.update` / `view.delete` without names). A *Views* menu (or <kbd>V</kbd>) applies one with *Undo*; editors and owners save views, and a view's creator or an owner updates, renames or deletes it; *Copy link* shares `?view=<id>`.
- A 3D view of the case graph (*2D | 3D* on the toolbar, kept in the URL as `?mode=3d` and remembered per browser): the same layout, pins, selection, filters and groups, with depth by first seen (oldest at the back, undated entities on their own plane, a date ruler), by entity type, or flat. Orbit, pan, zoom toward the pointer, fly-to, box selection, drag to pin (the same pins as 2D), the node menu, a minimap and the full keyboard map with the same announcements. Loaded only when used (three.js and React Three Fiber in their own chunk, about 200 KiB gzip); without WebGL the graph stays in 2D with a notice.
- Saved views store version 2 of the view state: `mode` (`2d` or `3d`), an optional `camera3d` and the `depth`. Version-1 views still load, in 2D.
- `make web-size` (run in CI) checks the built bundle against `apps/web/bundle-budget.json`.
- The sign-in page shows a slow 3D loom beside the form (behind it on narrow screens): a brass weft passing over and under the warp threads, knots forming where it crosses. It is fetched only after the form is ready and the browser is idle, from the 3D view's three.js chunk, at most 30 frames a second, paused while hidden or off-screen; a still drawing of the same loom is shown with reduced motion, Save-Data, without WebGL, or if it fails to load. `make web-size` also checks that the sign-in page's initial JavaScript has no three.js.
- Every page has its own title ("Cases · Nightloom", "*case* · Graph · Nightloom", …), the 404 page has the new look and shows the address, and Nightloom has a knot-on-thread app icon (SVG and PNG sizes) and a web manifest.
- Loading states show what is loading over still skeleton lines; a page whose list fails to load offers *Try again*.
- `make screenshots` takes the README screenshots (`docs/images/`) on a throwaway stack of its own with fictional demo data.
- Browser tests of the sign-in loom and axe-core accessibility checks (`@axe-core/playwright`) of every screen in both themes.

### Changed

- The graph's initial view and *Reset view* fill 85% of the canvas left free by the legend, the zoom controls and the minimap (2D and 3D), instead of leaving wide margins; a tiny graph is not blown up.
- 2D labels never overlap: the canvas places them itself, keeping only those with room, except the hovered, selected and keyboard-focused entities' labels, which always show.
- One focus ring everywhere: text fields now get the same 2px brass outline as buttons and links. The edges of text fields, selects and switches use a new `--control-line` token with at least 3:1 contrast in both themes.
- The contrast test reads the stylesheets, so every text color in use, and every text and background pair declared together, is checked in both themes.
- `prefers-reduced-motion` now ends every CSS animation and transition at once.
- The case graph's outer region is named "Case graph" (it had the same name as the graph stage inside it).

## [0.1.0] - 2026-11-19

First release: self-hosted, passive collection of public infrastructure data into per-case investigation graphs.

### Added

#### Investigations and ethics guardrails

- Investigation cases with a mandatory purpose (30–2,000 characters), a legal basis (owned assets, authorized engagement, threat intelligence, journalism, academic research, self-assessment or other) and an acknowledgement of the [ethics policy](https://github.com/TheRealNightmare/nightloom/blob/main/ETHICS.md) version in force.
- Case membership with owner, editor and viewer roles; only members can see a case, admins included.
- Case lifecycle: open, closed, archived and deleted; changing the purpose or legal basis requires a reason.
- Person-related entity types are off by default and can only be enabled per case with a written reason.
- Per-case activity feed built from the audit log.

#### Graph and entity model

- Shared entity model (`nightloom-core`) with 19 entity types, each with a sensitivity (infrastructure, organization or person) and validated properties.
- Canonical keys, so the same entity found in different spellings becomes one node; identical keys on every supported Python version.
- Relationship allow-list per pair of entity types, and provenance on every entity and relationship (source, first and last seen, confidence, Admiralty reliability and credibility).
- Per-case graphs stored in Neo4j; manual entities and relationships from the web UI.
- `GET /schema` exposes types, sensitivities and the relationship allow-list to the UI.
- Export as JSON (with provenance), GraphML or CSV (ZIP); person-related values are redacted by default, and including them requires a case owner and a recorded reason. Exports are limited in size and rate.

#### Shuttles

- Shuttle SDK (`nightloom-sdk`): the Shuttle contract and metadata, an entry-point registry, test helpers and a guarded HTTP client (SSRF checks after DNS resolution, per-host rate limits, response size and time limits).
- Background jobs on a Celery worker with Valkey: queued, running, succeeded, failed and cancelled states, shown live in the case graph's *Jobs* panel.
- `registrable-domain`: links a subdomain to its registrable domain using the bundled Public Suffix List; no network.
- `dns`: public DNS records (A, AAAA, CNAME, MX, NS, TXT, SOA, CAA, DMARC) through public resolvers only.
- `reverse-dns`: PTR names for an address or a netblock of up to 256 addresses, with forward confirmation.
- `crtsh`: subdomains and recent certificates from Certificate Transparency logs via crt.sh.
- `rdap`: registration data for domains, IP addresses, netblocks and ASNs; keeps organization names only, never contact details.
- `ripestat`: announced prefixes, origin ASes and AS holders from RIPEstat.
- `internetdb`: open ports, CPEs, CVE IDs, tags and hostnames from Shodan InternetDB (free for non-commercial use only).
- `wayback`: archived URLs and subdomains from the Wayback Machine; query strings reduced to parameter names, URLs containing email addresses or credentials dropped.

#### Security

- Server-side sessions in `HttpOnly`, `SameSite=Strict` cookies (`Secure` in production), with idle and absolute expiry and instant revocation.
- CSRF protection (double submit, bound to the session) on every state-changing request.
- Argon2id password hashing off the event loop, with a 12–128 character policy.
- Admin, analyst and viewer roles; the last active admin cannot be demoted or deactivated.
- Sign-in rate limits per email and per client IP, failing closed when Valkey is unavailable; uniform errors and timing for unknown accounts.
- Real client IPs only from trusted proxies (nginx, and Caddy or cloudflared in production).
- Security headers (CSP, `X-Frame-Options` and others) from nginx and the API; production refuses to start with placeholder secrets.

#### Audit log

- Append-only, hash-chained audit log in PostgreSQL for sign-ins, password changes, rate limiting, user management, authorization denials, case and graph changes, jobs and exports, with request IDs.
- Admin *Audit log* page with filters (action, outcome, actor, case, dates), event details and chain verification.

#### Deployment and operations

- Docker Compose stack: API, worker, web (nginx), PostgreSQL, Neo4j Community, Valkey and SeaweedFS (S3-compatible).
- Production overrides: only the edge publishes ports; HTTPS through Caddy (automatic certificates) or Cloudflare Tunnel; log rotation and memory limits; a small-host variant for 8 GB machines such as a Raspberry Pi 4B.
- `make secrets` generates a production `.env` with random secrets.
- `make backup` and `make restore`: PostgreSQL, Neo4j and the evidence bucket in one checksummed archive, with a manifest.
- Multi-architecture (`linux/amd64`, `linux/arm64`) images published to GHCR as `ghcr.io/therealnightmare/nightloom-backend` and `ghcr.io/therealnightmare/nightloom-web`, with SBOMs and build provenance attestations; production pulls them by `NIGHTLOOM_VERSION`.
- Release automation: version consistency checks, a tag-triggered release workflow and `make bump-version`.

#### Web UI

- React + TypeScript single-page app with sign-in, session handling and a forced password change for temporary passwords.
- Case list, case creation form and case workspace (graph, members, activity, settings).
- Interactive Sigma.js graph: add entities and relationships, side panel with provenance and connections, search, legend, run Shuttles on the selected entity.
- Graph filters (source, sensitivity, time seen, confidence, reliability and credibility, focus, isolated entities) kept in the URL; large groups of leaf neighbours collapsed into one node.
- Admin *Users* page: create users with a temporary password shown once, change roles, deactivate and reactivate, revoke sessions, reset passwords.
- Version shown in the account menu.

### Known limitations

- No auto-pivot yet: every Shuttle is run by hand on one entity.
- Passive Shuttles only; nothing contacts the investigated targets directly.
- Single-node deployment only (one host running the whole Compose stack).
- Backups stop Neo4j briefly, because Neo4j Community Edition can only be dumped while stopped.

[Unreleased]: https://github.com/TheRealNightmare/nightloom/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/TheRealNightmare/nightloom/releases/tag/v0.1.0
