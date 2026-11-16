# Architecture

> **Status: planned.** Nothing here is implemented yet. This document describes the intended design and will change as the code lands.

## Components

```mermaid
flowchart LR
    web["web<br/>React + Vite + Sigma.js"] -->|HTTPS / JSON| api["api<br/>FastAPI"]

    api --> pg[("PostgreSQL<br/>users, cases, jobs, audit log")]
    api --> neo[("Neo4j<br/>investigation graph")]
    api --> s3[("S3 object storage<br/>SeaweedFS by default<br/>evidence files")]
    api -->|enqueue jobs| valkey[("Valkey<br/>broker + cache")]
    api --> llm["LLM providers<br/>via LiteLLM"]

    valkey -->|consume jobs| worker["worker<br/>Celery"]
    worker --> shuttles["Shuttles<br/>(plugins)"]
    shuttles -->|public sources| internet(("Public data<br/>sources"))
    worker -->|entities + edges| neo
    worker -->|evidence| s3
    worker -->|job status| pg
```

| Component | Role |
| --- | --- |
| `apps/web` | React UI: graph view, case management, provenance panel |
| `apps/api` | FastAPI: auth, cases, graph queries, job submission, AI endpoints |
| `apps/worker` | Celery workers that run Shuttles and auto-pivot scans |
| `packages/sdk` | Shuttle SDK: base classes, entity types, test helpers |
| `shuttles/` | Built-in Shuttles |
| PostgreSQL | Users, cases, jobs, audit log, encrypted API keys |
| Neo4j | Entities and relationships |
| Valkey (Redis-compatible) | Celery broker, rate-limit counters, result cache |
| S3-compatible object storage (SeaweedFS by default) | Evidence files (screenshots, raw responses, documents) |

### Infrastructure choices

The local stack is defined in [`deploy/compose.yaml`](../deploy/compose.yaml) (see [Running the stack](../CONTRIBUTING.md#running-the-stack)).

- **Object storage is accessed only through the S3 API**, so any S3-compatible service works. The stack ships [SeaweedFS](https://github.com/seaweedfs/seaweedfs) (Apache-2.0) as the default. MinIO was the original plan, but its community edition no longer publishes Docker images and has been archived.
- **Valkey instead of Redis.** [Valkey](https://valkey.io/) is a BSD-licensed, Redis-compatible fork. Redis itself is no longer under a permissive open-source license. Clients talk the Redis protocol, so standard Redis libraries work unchanged.
- **Neo4j Community Edition** (GPLv3) runs as a separate server that Nightloom talks to over Bolt. It is not bundled into Nightloom's code.

### Web UI

[`apps/web`](../apps/web/) is a single-page app built with React, Vite and TypeScript (strict). The graph is a [graphology](https://graphology.github.io/) graph rendered by [Sigma.js](https://www.sigmajs.org/) (WebGL), laid out with ForceAtlas2 for a bounded number of iterations so the page never blocks on layout.

- **Same-origin API.** The UI only calls relative `/api/...` URLs. In development the Vite dev server proxies them to `http://127.0.0.1:8000`; in the Compose stack the `web` container (unprivileged nginx) serves the built files and proxies `/api/` to `http://api:8000`. No CORS configuration is needed.
- **Static hosting.** nginx falls back to `index.html` for client-side routes, caches the content-hashed `/assets/` for a year, revalidates `index.html` on every load, and sends a strict Content Security Policy (same origin only, plus `blob:` for workers and images) with other security headers.
- **API status.** Once signed in, the top bar polls `GET /api/v1/ready` every 15 seconds and shows *Ready*, *Degraded* (listing the failing checks) or *API offline*.
- **Sign-in.** See [Web authentication flow](#web-authentication-flow).
- **Demo data.** Until the graph API exists, the UI shows a typed fixture ([`src/demo/sampleGraph.ts`](../apps/web/src/demo/sampleGraph.ts)) shaped like the [data model](#data-model): entity types and provenance fields on every node and edge. It is entirely fictional (`example.org`, RFC 5737 IP addresses, RFC 5398 AS numbers, no people) and labelled "Demo data" in the UI.
- **Testable without WebGL.** Highlighting, filtering and search are pure functions over the graph; sigma only applies their results through its node and edge reducers.

### Health and readiness

The API creates its clients (async SQLAlchemy engine, Neo4j driver, Valkey client, S3 client) in the FastAPI lifespan, but none of them connects at startup. The API therefore always starts, even when every dependency is down.

| Endpoint | Kind | Behaviour |
| --- | --- | --- |
| `GET /api/v1/health` | Liveness | Always `200` while the process is serving requests. Never touches a dependency. |
| `GET /api/v1/ready` | Readiness | Pings PostgreSQL (`SELECT 1`), Neo4j (`RETURN 1`), Valkey (`PING`) and object storage (`HeadBucket` on the evidence bucket) concurrently, each limited to `NIGHTLOOM_DEPENDENCY_TIMEOUT_SECONDS` (default 2). `200` when all pass, `503` otherwise. |

The readiness body reports `ok`, `latency_ms` and a short `error` for each dependency. Errors are redacted before they are returned or logged: they never include credentials or connection URLs. Use `/health` for container restarts and `/ready` for traffic routing and monitoring.

### Authentication

The API authenticates browsers with **server-side sessions** (code in [`nightloom_api/auth/`](../apps/api/src/nightloom_api/auth/)). There is no public registration: the first admin is created with `make create-admin`, and admins create everyone else.

**Sessions.** A successful `POST /api/v1/auth/login` creates a random 256-bit token (`secrets.token_urlsafe(32)`) and sends it only in the `nightloom_session` cookie: `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` in production (or with `NIGHTLOOM_COOKIE_SECURE=true`). PostgreSQL stores only the token's SHA-256 in the `sessions` table, so a leaked table cannot be replayed. A session expires after **24 hours without use** (sliding; `last_seen_at` is written at most once a minute) and, at the latest, **7 days after sign-in**. Expired sessions are deleted when they are next presented and when their user signs in again. Logging out deletes the row. Changing your password signs out your other sessions; deactivating a user or revoking their sessions (admin) signs them out everywhere.

**CSRF.** Login also sets a non-HttpOnly `nightloom_csrf` cookie. Every authenticated `POST`, `PUT`, `PATCH` or `DELETE` must repeat it in the `X-CSRF-Token` header (double submit, constant-time comparison), or it gets `403`. The CSRF token is derived from the session token with a domain-separated SHA-256, so a cookie planted by an attacker (for example from a sibling subdomain) does not match the victim's session. Login itself is exempt. `SameSite=Strict` is a second, independent layer.

**Passwords.** Hashed with Argon2id ([pwdlib](https://github.com/frankie567/pwdlib) on argon2-cffi, its recommended parameters); a hash with outdated parameters is upgraded on the next successful sign-in. The policy is 12–128 characters, not equal to or containing the local part of the user's email (for local parts of three or more characters). Emails are stored trimmed and lower-case, and are unique.

**Roles.** `admin`, `analyst` and `viewer`. Routes use the `current_user` dependency (`401` without a valid session) and `require_role(...)` (`403`, recorded in the [audit log](#audit-log)). Only admins manage users. An admin cannot demote or deactivate themselves, and the last active admin cannot be demoted or deactivated (`409`); concurrent changes are serialized with row locks.

**Brute-force protection.** Failed sign-ins are counted in Valkey, per email (hashed, so Valkey holds no addresses) and per client IP, in 15-minute windows. After **5 failures for an email** or **20 from an IP**, further attempts get `429` with `Retry-After` until the window ends, without checking the password. A successful sign-in resets the email's counter. Every failure returns the same `401 "Invalid email or password."`, and an unknown email still runs a full Argon2 verification, so neither the message nor the timing reveals whether an account exists. Wrong current passwords on `POST /auth/password` count against the same limits. If Valkey is unreachable, sign-in fails closed with `503`.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `POST /auth/login` | anyone | `{email, password}` → `{user}` and both cookies |
| `POST /auth/logout` | signed in | `204`, clears the cookies |
| `GET /auth/me` | signed in | the current user |
| `POST /auth/password` | signed in | `{current_password, new_password}` → `204` |
| `GET /users`, `POST /users` | admin | list; create with an initial password |
| `PATCH /users/{id}` | admin | `display_name`, `role`, `is_active` |
| `DELETE /users/{id}/sessions` | admin | sign the user out everywhere |
| `GET /audit` | admin | the [audit log](#audit-log), newest first |
| `POST /audit/verify` | admin | check the audit log's hash chain |

`/health` and `/ready` stay public. Request validation errors (`422`) never echo the submitted values, so a password cannot be reflected back.

**Why not JWT?** The browser is the only client, and it talks to the API on the same origin. Opaque session tokens checked against PostgreSQL can be revoked instantly (logout, password change, deactivation, admin revocation) and reflect role changes on the next request; a stateless JWT stays valid until it expires unless a server-side denylist is added, which brings back the database lookup without the simplicity. Keeping the token in an HttpOnly cookie also keeps it away from page scripts, whereas a bearer token readable by JavaScript is exposed to any XSS.

**Production safety.** With `NIGHTLOOM_ENV=production`, the API refuses to start while `NIGHTLOOM_DATABASE_URL`, `NIGHTLOOM_NEO4J_PASSWORD`, `NIGHTLOOM_VALKEY_URL`, `NIGHTLOOM_S3_ACCESS_KEY` or `NIGHTLOOM_S3_SECRET_KEY` still contains the `change-me` placeholder. The error names the variables, never their values.

**Hashing off the event loop.** An Argon2 check costs roughly a tenth of a second of CPU. Request handlers never run it on the event loop: `passwords.*_async` hand it to a worker thread, and a shared limiter lets at most `NIGHTLOOM_PASSWORD_HASH_CONCURRENCY` (default 4, at least 1) hashes run at once. Other requests stay responsive during a burst of sign-ins, and a sign-in flood queues for hashing instead of taking every worker thread and CPU core. The CLI keeps the synchronous functions.

**Client IP behind the proxy.** The per-IP limit and each session's recorded `ip` use the client's address. In the Compose stack the browser talks to nginx (`web`), which forwards `/api/` with `X-Real-IP: $remote_addr` and `X-Forwarded-For: $proxy_add_x_forwarded_for` (the client's header with the peer nginx saw appended). uvicorn (`--proxy-headers`) honours forwarding headers only from the addresses in `FORWARDED_ALLOW_IPS`; Compose sets that to exactly the `web` container's address, which is fixed (`NIGHTLOOM_WEB_IP`, default `172.30.0.10`, on the fixed subnet `NIGHTLOOM_NETWORK_SUBNET`, default `172.30.0.0/24`). Docker hands out addresses to every other container only from `NIGHTLOOM_NETWORK_IP_RANGE` (default `172.30.0.128/25`), which excludes the web address, so no other container can ever hold the trusted address, for example after `web` is recreated. uvicorn then takes the right-most address in `X-Forwarded-For` that is not a trusted proxy, which is the one nginx appended, so a value the client forges at the front of the header never wins. Requests that reach the API directly (port 8000) come from an untrusted peer, so their forwarding headers are ignored and the peer address is used. `FORWARDED_ALLOW_IPS` is never `*`: that would let any client choose its own IP and dodge the per-IP limit. Outside Compose, uvicorn's default trusts only `127.0.0.1`; set `FORWARDED_ALLOW_IPS` to your proxy's address when you put another one in front.

**Accepted trade-off: email lockout.** Five failed attempts lock an email for the rest of its 15-minute window, whoever made them. Someone who knows a user's email can therefore keep that user from signing in by sending wrong passwords, for at most 15 minutes per five attempts, and must keep doing so to extend it. This is accepted because the per-email limit is the main protection against password guessing spread across many IPs, which the per-IP limit alone cannot stop; the window bounds the harm, and the lock clears by itself when it ends.

### Web authentication flow

The web UI ([`apps/web/src/auth/`](../apps/web/src/auth/), [`src/api/client.ts`](../apps/web/src/api/client.ts)) never sees the session token: it lives only in the `HttpOnly` cookie, which the browser sends on every same-origin request (`credentials: "same-origin"`).

- **Start-up.** `AuthProvider` asks `GET /auth/me`. Until it answers, a splash screen is shown (no flash of the sign-in form); `200` shows the app, anything else the sign-in screen.
- **Sign-in.** The form posts to `/auth/login`. The UI shows only fixed messages: *Invalid email or password.* for `401`, *Too many attempts. Try again in N minutes.* for `429` (from `Retry-After`, rounded up), and generic network, timeout and unavailable messages. Server text is never shown, except validation messages (`422`).
- **CSRF.** For every `POST`, `PUT`, `PATCH` and `DELETE` the client reads the `nightloom_csrf` cookie and repeats it in the `X-CSRF-Token` header; `GET` requests carry no header.
- **Session expiry.** A `401` on any request other than sign-in means the session ended (expired, revoked by an admin, or signed out by a password change elsewhere). The client notifies `AuthProvider`, which drops the user and shows the sign-in screen with *Your session has ended. Please sign in again.*
- **Sign-out.** Calls `POST /auth/logout`, then clears the local state even if that call fails; an orphaned server session still expires on its own.
- **Password change.** A modal checks the policy locally (12–128 characters, confirmation matches, differs from the current one), then posts to `/auth/password`; the server checks again, and its validation messages are shown next to the fields. On success the other sessions are signed out and this one stays.

### Request IDs

Every request gets an ID that ties together the API's log lines, its audit events and the response. A pure-ASGI middleware ([`request_id.py`](../apps/api/src/nightloom_api/request_id.py); not `BaseHTTPMiddleware`, which runs the app in another task and buffers responses) accepts an incoming `X-Request-ID` only if it matches `^[A-Za-z0-9._-]{8,64}$` and otherwise generates a UUID4. The ID lives in a context variable for the duration of the request, a logging filter adds it to every log record (`... name [request-id]: message`), and the response returns it in `X-Request-ID`. In the Compose stack nginx applies the same rule: it forwards a valid client ID, or its own random `$request_id` instead, and writes the ID into its access log, so nginx, the API and the audit log all show the same value.

### Audit log

The audit log (code in [`nightloom_api/audit/`](../apps/api/src/nightloom_api/audit/)) records security-relevant actions in the PostgreSQL table `audit_events`. Only admins can read it.

**What is recorded.** One event per action, with when it happened (`occurred_at`, the database time), who did it (`actor_user_id` and `actor_label`, the actor's email *at that time*, so history survives later changes), from where (`actor_ip`, from `request.client` as described in [Client IP behind the proxy](#authentication)), the request ID, the `action`, the `outcome` (`success`, `failure` or `denied`), what it was done to (`target_type`, `target_id`), a nullable `case_id` (set on every [case](#cases) event, including refused case requests), and a small `details` object.

| Action | Recorded when |
| --- | --- |
| `auth.login` | a sign-in succeeds or fails (unknown email, wrong password, inactive user, over-long password) |
| `auth.logout` | a user signs out |
| `auth.password_change` | a password is changed, or the current password given was wrong (a new password that fails the policy is a validation error and is not recorded) |
| `auth.rate_limited` | a sign-in or password change is refused with `429` |
| `user.create`, `user.update`, `user.sessions_revoke` | an admin creates or changes a user or signs them out everywhere, or tries to and fails (`409`, `404`, `422` on create); `user.update` lists each changed field's old and new value. `create-admin` on the command line is recorded with the actor `system:cli`. |
| `authz.denied` | a signed-in user is refused by a role check (`403`), with the method, route and roles; or a case request is refused (`404` for non-members, `403` for a missing case capability), with `case_id` set and a `reason` (`not_a_member` or `missing_capability`) |
| `audit.verify` | an admin runs the chain verification, with its result |
| `case.create` | a case is created: legal basis, `allow_person_entities`, retention, ethics version and, when person entities are allowed, the `person_entities_reason` |
| `case.update` | case fields change: the changed field **names** only, plus `change_reason` when one was required; never the text of a title, description or purpose |
| `case.status_change` | a case's status changes (`from`, `to`) |
| `case.delete` | a case is deleted (its member count; not its title), or the title confirmation did not match (`failure`) |
| `case.member_add`, `case.member_update`, `case.member_remove` | membership changes (role, or `from`/`to`; `self` on removal), or fails (`409` last owner, `404` unknown user or member); the target is the member's user ID |

**What is never recorded.** Passwords, password hashes, session and CSRF tokens, cookies, request bodies and secrets from the configuration. `details` is built from a few named fields, never a whole request body, and passes a sanitizer that drops any key containing `password`, `token`, `secret`, `cookie`, `csrf` or `hash` (case-insensitive, at any depth), cuts strings at 512 characters and replaces a value over 8 KB with a size marker. A failed sign-in stores no email at all: `actor_label` stays empty and `details` holds only the SHA-256 of the normalized address (`email_sha256`), so repeated attempts can be correlated without keeping typed addresses (or passwords pasted into the email field). That digest is pseudonymous, not anonymous: anyone who already has a candidate address can compute it. Reading the log is not itself recorded.

**Same transaction.** A successful change records its event in the same database transaction, right before the commit, so the change and its event commit or roll back together: a rolled-back change leaves no event. Failures and refusals (failed sign-ins, `429`, `403`, failed admin actions) describe something that did not happen, so they are written in their own short transaction; if that write fails, the error is logged and the client still gets its `401`/`403`/`429`.

**Hash chain.** Each event's `hash` is the SHA-256 of a canonical JSON document of every other column of the row, including `prev_hash` (keys sorted, no whitespace, UTF-8, timestamps as UTC ISO 8601 with microseconds, UUIDs and IP addresses in standard text form). `prev_hash` is the previous event's `hash`, and 64 zeros for the first event. Appends are serialized: inside the transaction the appender takes `pg_advisory_xact_lock` on a fixed key, draws the next ID, reads the latest hash and inserts its row; the lock is held until the transaction ends, so ID order is chain order. `hash` and `prev_hash` are both unique, so the chain cannot fork. Floats in `details` are stored as text, because `jsonb` may rewrite a number's form (`1e100` comes back as an integer) and change the hash.

**Verification.** `POST /api/v1/audit/verify` walks the chain in ID order in batches of 1,000 rows (keyset pagination), recomputes each hash in a worker thread so the event loop is never blocked for long, and returns `{ok, checked, first_bad_id, reason}`: the first event whose `prev_hash` does not match its predecessor (an event was removed or reordered) or whose contents do not match its `hash` (an event was edited). Only one verification runs at a time (a Valkey lock; others get `429`), and each run is itself recorded as `audit.verify`.

**Append-only.** Migration `0003_audit_events` installs a trigger that raises on every `UPDATE`, `DELETE` and `TRUNCATE` of `audit_events`, so neither the application nor a mistaken query can change history. For the same reason the foreign key from `actor_user_id` to `users` is `ON DELETE RESTRICT`: setting it to `NULL` would rewrite the event and break its hash, so a user with audit history can be deactivated but not deleted.

**Listing.** `GET /api/v1/audit` returns events newest first (by ID) as `{items, next_before_id}`; pass `next_before_id` back as `before_id` for the next page. `limit` is 1–200 (default 50). Filters: `action`, `outcome`, `actor_user_id`, `case_id`, `since` (inclusive) and `until` (exclusive), the last two timezone-aware. `prev_hash` and `hash` are left out unless `include_hashes=true`.

**Honest limits.**

- The trigger and the chain stop accidental changes and make edits by the application visible; they do not stop someone with full control of the database. The API's database role owns the table, so it (or a PostgreSQL superuser) can disable the trigger, rewrite events and recompute every later hash, and verification would pass. Cutting off the newest events also leaves a valid, shorter chain. Mitigations for later: periodically anchor the latest hash outside the database (an external append-only store, a signed timestamp, or simply a copy kept by an operator), so a rewritten or shortened chain no longer matches the anchor; and run the API with a role that does not own `audit_events` and has only `INSERT` and `SELECT` on it.
- The log only knows what the API tells it. Actions taken directly in the database, and events whose own-transaction write failed (logged as an error), are not in it.
- Adding a column to `audit_events` changes what is hashed; it will need a format version in the canonical document so older events still verify.

**No foreign key to cases.** `audit_events.case_id` deliberately does not reference `cases`. A case's audit trail must outlive the case (so an admin can still read it with `GET /audit?case_id=...` after deletion), and audit rows can never change, which rules out both `ON DELETE SET NULL` and `CASCADE`.

### Cases

A case is one investigation (code in [`nightloom_api/cases/`](../apps/api/src/nightloom_api/cases/)). Every case states **why** it exists before anything is collected, as [ETHICS.md](../ETHICS.md) requires.

| Field | Rules |
| --- | --- |
| `title` | 3–200 characters |
| `description` | optional, at most 5,000 characters |
| `purpose` | **required**, 30–2,000 characters |
| `legal_basis` | **required**, one of the bases below |
| `legal_basis_note` | at most 1,000 characters; **required** for `authorized_engagement` and `other` |
| `ethics_ack_version`, `ethics_ack_at` | creating a case requires `ethics_acknowledged: true`; the server stores the version of ETHICS.md in force (`ETHICS_VERSION`, currently `2026-11`) and the time |
| `allow_person_entities` | default `false`. Turning it on at creation needs a `person_entities_reason` (at least 30 characters), which is stored only in the `case.create` audit event |
| `status` | `open` (default), `closed` or `archived`. Closing sets `closed_at`; reopening clears it; archiving leaves it as it was |
| `retention_days` | optional, 1–3,650. **Not enforced yet**: nothing deletes old case data automatically |
| `created_by`, `created_at`, `updated_at`, `closed_at` | set by the server |

Legal bases: `owned_assets`, `authorized_engagement`, `threat_intelligence`, `journalism`, `academic_research`, `self_assessment`, `other`. The same rules are CHECK constraints in PostgreSQL (migration `0004_cases`), so a bug in the API cannot store a case without them.

**Who may do what.** Only users whose global role is `admin` or `analyst` can create cases; the creator becomes the case's owner. After that, access comes **only** from case membership (`case_members`: one role per user and case):

| Capability | viewer | editor | owner |
| --- | :-: | :-: | :-: |
| Read the case and its member list | ✓ | ✓ | ✓ |
| Change title and description | | ✓ | ✓ |
| Run Shuttles (when they exist) | | ✓ | ✓ |
| Read the case's audit events | | ✓ | ✓ |
| Change purpose, legal basis, status, `allow_person_entities`, retention | | | ✓ |
| Add members, change roles, remove others | | | ✓ |
| Delete the case | | | ✓ |
| Leave the case (remove yourself) | ✓ | ✓ | ✓ |

- **Admins have no implicit access.** A global admin who is not a member of a case cannot read or change it, like anyone else. Case data stays with the people working on the case; admins manage accounts, not investigations.
- **Global viewers are always case viewers.** They can only be added with the `viewer` case role (`422` otherwise), and if a member's global role is later changed to `viewer`, their case role is treated as `viewer` everywhere.
- **404, not 403, for outsiders.** A user who is not a member gets `404 Case not found.` for every case route, exactly as for a case that does not exist, so case IDs reveal nothing. A member without the needed capability gets `403`. Both are recorded as `authz.denied` with the case's ID.
- **At least one owner.** Demoting or removing the last owner (including an owner removing themselves) is refused with `409`. Only active owners whose global role is not `viewer` count, because only they can manage the case. Membership changes lock the case row (`FOR NO KEY UPDATE`) and re-read the caller's role after the lock is granted, so concurrent changes are serialized and cannot both remove "the other" owner.
- **Reasons for sensitive changes.** Changing `purpose`, `legal_basis`, `legal_basis_note` or `allow_person_entities` requires a `change_reason` (10–500 characters), stored in the `case.update` event. Setting a field to the value it already has is not a change.
- **Deletion.** The owner repeats the exact title (`confirm_title`, case-sensitive). The case and its memberships are deleted in one transaction; the audit events stay. Data that will belong to a case later (graph entities, evidence objects) is removed by a hook in the same transaction once those features exist.

| Endpoint (under `/api/v1`) | Who | Notes |
| --- | --- | --- |
| `POST /cases` | admin, analyst | `201`, the caller becomes owner |
| `GET /cases` | signed in | the caller's cases with `my_role`, most recently updated first; `{items, next_cursor}`, pass `next_cursor` as `cursor`; `limit` 1–200 (default 50); filters `status` and `q` (case-insensitive title search; `%`, `_` and `\` match literally) |
| `GET /cases/{id}` | member | |
| `PATCH /cases/{id}` | editor, owner | editors: `title`, `description`; owners: every field |
| `DELETE /cases/{id}` | owner | body `{confirm_title}`; `204`, or `422` if it does not match |
| `GET /cases/{id}/members` | member | owners first |
| `PUT /cases/{id}/members` | owner | `{email, role}`: adds an active user (`201`) or changes their role (`200`); unknown or inactive email: `404 No active user with that email.` |
| `DELETE /cases/{id}/members/{user_id}` | owner, or the member themselves | `204`; `409` for the last owner |
| `GET /cases/{id}/audit` | editor, owner | the case's audit events, paged and filtered like `GET /audit` (without `actor_user_id`, `case_id` and hashes) |

### Migrations

The PostgreSQL schema is managed by [Alembic](https://alembic.sqlalchemy.org/) in [`apps/api/alembic/`](../apps/api/alembic/). ORM models derive from `nightloom_api.infra.db.Base`, whose metadata has an explicit naming convention, so constraint and index names are stable across autogenerated revisions. The history starts at an empty `0001_baseline` revision; `0002_users_sessions` adds the `users` and `sessions` tables and the `user_role` enum; `0003_audit_events` adds the [audit log](#audit-log) and its append-only trigger; `0004_cases` adds [`cases` and `case_members`](#cases) with their enums (and intentionally no foreign key from `audit_events.case_id`). In the Compose stack, a one-shot `migrate` service runs `alembic upgrade head` before the API starts.

## Data model

Entity types are inspired by STIX 2.1:

| Group | Types |
| --- | --- |
| Infrastructure | Domain, Subdomain, IP, ASN, Netblock, Certificate, URL, Port/Service, Technology |
| Organizations and people | Organization, Person, Email, Username, SocialProfile, Phone |
| Other | Breach, Location, Image, Document, CryptoWallet |

### Provenance

Every node **and** edge carries:

| Field | Meaning |
| --- | --- |
| `source` | The Shuttle that produced it |
| `first_seen` / `last_seen` | When it was first and most recently observed |
| `confidence` | 0–1 |
| `admiralty` | Admiralty rating, `A1`–`F6` (source reliability × information credibility) |
| `evidence` | Pointer to the evidence object in S3 object storage |
| `case_id` | The case it belongs to |

### Deduplication

Each entity type defines a **canonical key** (e.g. lower-cased FQDN for a Domain, normalized address for an IP). Writes are upserts on `(case_id, type, canonical_key)`, so the same entity found by several Shuttles becomes one node with multiple provenance records.

## Shuttle contract

A Shuttle is a plugin that takes one entity and returns entities and edges. Each declares:

| Field | Description |
| --- | --- |
| `name` | Unique identifier |
| `input_types` | Entity types it accepts |
| `output_types` | Entity types it can produce |
| `mode` | `passive` (third-party sources only) or `active` (touches the target) |
| `requires_api_key` | Whether a user-supplied key is needed |
| `rate_limit` | Per-source request limit |
| `risk_level` | `infrastructure` or `person` |

Active Shuttles run only against assets with verified ownership. Person-related Shuttles are gated per case and never run during auto-pivot.

## Auto-pivot

Auto-pivot expands a seed entity breadth-first:

- **Max depth** — number of hops from the seed
- **Budget** — maximum requests and/or nodes per scan
- **Entity-type allowlist** — only pivot through selected types
- **Passive-only mode** — skip all active Shuttles
- Person-related Shuttles are always excluded

The scan stops when the queue is empty or any limit is reached; partial results are kept.

## AI layer

- **LiteLLM** provides one interface to any provider, including local models via Ollama.
- **Per-user API keys** — encrypted at rest with AES-GCM, bound to the owning `user_id` (as associated data), decrypted only server-side when needed, and **never sent to the browser**.
- **Per-feature model routing** — each user picks which model handles each feature (e.g. summaries vs. query generation).
- **Natural language → Cypher** — generated queries run as a **read-only** Neo4j user, so the AI can never modify the graph.

## Guardrails

Collection rules, gated Shuttles, audit logging, PII redaction and data retention are defined in [ETHICS.md](../ETHICS.md). They are design requirements, not optional features.
