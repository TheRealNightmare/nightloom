# Deploying Nightloom

This guide runs Nightloom on your own server with HTTPS, secrets generated for you, and nightly backups. It uses the same Docker Compose stack as development ([`deploy/compose.yaml`](../deploy/compose.yaml)) with production overrides ([`deploy/compose.prod.yaml`](../deploy/compose.prod.yaml)):

- the API runs with `NIGHTLOOM_ENV=production`: it refuses to start with placeholder secrets, and its cookies are `Secure`;
- **only the edge proxy is reachable from outside.** PostgreSQL, Neo4j, Valkey, SeaweedFS, the API and nginx publish no ports;
- the edge is either **Caddy** (automatic HTTPS certificates, ports 80 and 443) or **Cloudflare Tunnel** (no open ports at all);
- every container has a restart policy, rotated logs (5 × 10 MB) and a memory limit;
- Nightloom itself runs from the **published release images** on GitHub Container Registry, `ghcr.io/therealnightmare/nightloom-backend` (API, worker and migrations) and `ghcr.io/therealnightmare/nightloom-web`, for `linux/amd64` and `linux/arm64`. `NIGHTLOOM_VERSION` in `.env` chooses the release. Nothing is built on the server.

Read [ETHICS.md](../ETHICS.md) before you deploy. Everyone you give an account to must follow it.

## Requirements

- A 64-bit Linux server (x86-64 or ARM64) that you control, with a fixed public IP address for the Caddy option. Any IP address works for the Cloudflare Tunnel option.
- [Docker Engine](https://docs.docker.com/engine/install/) with the Compose plugin **2.24.4 or newer** (the production overrides use `!reset` and `!override`). Check with `docker compose version`.
- `git`, `make`, `bash`, `openssl` (or `python3`), `tar` and `sha256sum`. Most distributions include all of these.
- Disk space for the data plus the backups. Use an SSD: Neo4j and PostgreSQL write a lot.

| Host | Compose files | Memory limits (sum) | Notes |
| --- | --- | --- | --- |
| 16 GB RAM (recommended) | `compose.yaml` + `compose.prod.yaml` | about 12 GB | Neo4j has a 2 GB heap and a 1 GB page cache. The worker runs 4 Shuttle jobs at a time. |
| 8 GB RAM, e.g. a Raspberry Pi 4B | add `compose.small.yaml` | about 5.3 GB | Neo4j has a 768 MB heap and a 256 MB page cache. The worker runs 1 job at a time. See [Small hosts and Raspberry Pi](#small-hosts-and-raspberry-pi). |

The limits are ceilings, not reservations. An idle stack uses much less.

## Quick deploy with Caddy (HTTPS)

1. **DNS.** Create an `A` record (and an `AAAA` record if the server has IPv6) for the name you will use, for example `nightloom.example.org`, pointing at the server. Wait until `dig +short nightloom.example.org` returns the server's address.
2. **Firewall.** Allow inbound TCP 80 and 443, plus UDP 443 for HTTP/3. Port 80 must stay open: Let's Encrypt uses it to issue certificates, and Caddy uses it to redirect to HTTPS.
3. **Get the release and create the secrets.** Check out the release you want to run (see [Releases](https://github.com/TheRealNightmare/nightloom/releases)), so the Compose files match its images:

   ```sh
   git clone --branch v0.2.0 https://github.com/TheRealNightmare/nightloom.git
   cd nightloom
   make secrets ARGS="--domain nightloom.example.org --acme-email you@example.org"
   ```

   See [Secrets](#secrets) for what this does. It also sets `NIGHTLOOM_VERSION=0.2.0`, the checkout's version.
4. **Download the images and start the stack:**

   ```sh
   make deploy-pull   # pulls the release images and the pinned third-party ones
   make up
   ```

   `make up` sees `NIGHTLOOM_ENV=production` in `.env` and adds `deploy/compose.prod.yaml` by itself. It also picks up the edge profile from `COMPOSE_PROFILES`. Without `make`, the equivalent commands are:

   ```sh
   docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml --env-file .env pull
   docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml --env-file .env up -d
   ```

   Caddy requests the certificate when it starts. `make logs SERVICE=caddy` shows the progress. If issuance fails, the cause is almost always DNS or a closed port 80.
5. **Create the first admin** (see [First admin](#first-admin)), then open `https://nightloom.example.org`.

Caddy renews certificates automatically. It keeps them, and its ACME account, in the `caddy_data` volume. Keep that volume: Let's Encrypt rate-limits re-issuing certificates.

What Caddy does ([`deploy/caddy/Caddyfile`](../deploy/caddy/Caddyfile)):

- redirects HTTP to HTTPS;
- sends `Strict-Transport-Security: max-age=31536000` (one year, this host only, no `preload`). Change it with `NIGHTLOOM_HSTS`. Only consider `preload` once you are sure the domain will stay HTTPS-only for good;
- refuses request bodies over 1 MiB, the same limit as nginx;
- logs requests to stdout as JSON, **without query strings** (they can contain search terms or entity values). It never logs cookies or `Authorization` headers.

The other security headers (CSP, `X-Frame-Options`, ...) come from nginx and the API, as in development. Caddy does not add second copies of them.

## Cloudflare Tunnel

Cloudflare Tunnel is an alternative to Caddy when you cannot or do not want to open ports. Examples are a home connection behind CGNAT, or a Raspberry Pi on a home network. `cloudflared` opens an outbound connection to Cloudflare, Cloudflare serves your hostname with its own certificate, and nothing on the server listens publicly.

Cloudflare terminates TLS, so Cloudflare can see the traffic between your users and Nightloom, including case data. Decide whether that is acceptable for your investigations. If it is not, use Caddy.

1. Add your domain to Cloudflare.
2. In the Cloudflare dashboard, go to **Zero Trust → Networks → Tunnels → Create a tunnel**, choose **Cloudflared** and name the tunnel.
3. Under *Install and run a connector*, copy the **token**: the long value after `--token` in the command it shows. Do not run that command. Docker Compose runs the connector for you.
4. Add a **public hostname**, for example `nightloom.example.org`, with service type **HTTP** and URL **`web:8080`**. That is the nginx container on the Compose network.
5. On the server:

   ```sh
   make secrets ARGS="--edge cloudflared"
   # put the token in .env:  TUNNEL_TOKEN=eyJ...
   make up
   ```

6. In Cloudflare, enable **SSL/TLS → Edge Certificates → Always Use HTTPS**. Also consider **HSTS** there: in this mode Caddy is not running, so Nightloom does not send HSTS itself.

`make secrets` sets `NIGHTLOOM_EDGE=cloudflared` and `COMPOSE_PROFILES=tunnel`. nginx then trusts `CF-Connecting-IP`, but only from the cloudflared container's fixed address (`172.30.0.12`). The audit log and the sign-in rate limits therefore see each user's real address. Change both variables together if you ever switch edges. See [Behind the production edge](ARCHITECTURE.md#authentication).

## Secrets

```sh
make secrets                  # Caddy edge; add --domain/--acme-email, or edit .env afterwards
make secrets ARGS="--help"    # all options: --edge, --domain, --acme-email, --small, --version, --force
```

[`scripts/generate-env.sh`](../scripts/generate-env.sh) creates `.env` from [`.env.example`](../.env.example):

- every `change-me-...` value gets a random 256-bit hex value. Hex is safe inside URLs, so `NIGHTLOOM_DATABASE_URL`, `NIGHTLOOM_VALKEY_URL` and `NIGHTLOOM_NEO4J_PASSWORD` repeat the generated passwords exactly;
- it sets `NIGHTLOOM_ENV=production`, `NIGHTLOOM_COOKIE_SECURE=true`, the edge (`NIGHTLOOM_EDGE` and `COMPOSE_PROFILES`), `NIGHTLOOM_VERSION` (the checkout's version, or `--version X.Y.Z`) and, if you pass them, the domain, the ACME email and the small-host switch;
- the file gets mode `600`. The script prints the **names** of the generated values, never the values.

It never overwrites an existing `.env`. PostgreSQL, Neo4j and Valkey take their passwords from the first start, so new secrets would lock the stack out of its own data. `--force` exists for a fresh install only: it copies the old file to `.env.backup-<timestamp>` first and prints a warning.

**Store a copy of `.env` somewhere safe**, for example in a password manager. Backups never include it, and a restore needs it.

## First admin

```sh
make create-admin email=you@example.org name="Your Name"
```

It prompts for a password (12–128 characters); this account is not asked to change it. There is no public sign-up: sign in at your domain, open **Users** in the top bar and create everyone else's accounts there (*New user*). Nightloom generates a temporary password for each new user and shows it once; pass it on through a secure channel, separate from the email address. The user must choose their own password at their first sign-in. *Reset password* on the same page does the same for a user who lost theirs, and signs them out everywhere. Give each person the lowest role that fits, and consider a second admin account so the first one is not the only way in.

## Backups

```sh
make backup
```

[`scripts/backup.sh`](../scripts/backup.sh) writes `backups/nightloom-<UTC timestamp>.tar.gz`, which contains:

| File | Contents |
| --- | --- |
| `postgres.dump` | `pg_dump -Fc` of the database: users, cases, memberships, jobs and the audit log |
| `neo4j.dump` | `neo4j-admin database dump` of the graph |
| `s3/` | every object in the evidence bucket, copied over the S3 API (rclone) |
| `manifest.json` | Nightloom version, commit and schema revision, PostgreSQL and Neo4j versions, image tags, start and end times, counts (users, cases, jobs, audit events, entities, relationships, objects) |
| `SHA256SUMS` | checksums of all of the above |

- **Short downtime.** The API and the worker are stopped while the backup runs, so all three stores are captured at the same moment. Neo4j Community Edition can only be dumped while it is stopped, so Neo4j is stopped too. A small instance takes about a minute. Whatever happens, even on failure, the script starts again everything it stopped.
- **Retention.** The newest `NIGHTLOOM_BACKUP_KEEP` archives are kept (default 7). Others are deleted. Set `NIGHTLOOM_BACKUP_DIR` to write somewhere other than `backups/`.
- **Not included:** `.env` (keep it separately), Valkey (only the job queue and rate-limit counters; a job still queued during a backup is not restored) and Caddy's certificates (Caddy issues them again).
- The archive holds everything in your instance: case data, password hashes and the audit log. Protect it like the server itself.

### Nightly backups with cron

As a user in the `docker` group, for example in `/etc/cron.d/nightloom`:

```cron
# m h dom mon dow user      command
15 3 * * *  nightloom  cd /opt/nightloom && make backup >> /var/log/nightloom-backup.log 2>&1
```

### Copy backups off the machine

A backup on the same disk does not survive a lost disk, a lost server or a compromised server. Copy every archive somewhere else after it is made, preferably **encrypted**, and to a place the server cannot delete from. For example, with [restic](https://restic.net/) or to an append-only remote:

```sh
restic -r sftp:backup@backup-host:/srv/restic/nightloom backup /opt/nightloom/backups
```

Test a restore now and then, on a spare machine (see below). A backup you have never restored is a hope, not a backup.

## Restore

```sh
make restore BACKUP=backups/nightloom-20261119T031500Z.tar.gz
```

[`scripts/restore.sh`](../scripts/restore.sh):

1. unpacks the archive into a temporary directory and checks `SHA256SUMS`. It refuses a damaged or modified backup, and a backup with files that the checksums do not cover;
2. shows the manifest and asks you to type `restore`;
3. stops `api`, `worker` and `web`, and starts PostgreSQL, Valkey and SeaweedFS;
4. **replaces** the data: it drops and recreates the PostgreSQL database and restores the dump, loads the Neo4j dump into the stopped database, and syncs the bucket to the backup's copy. Objects that are not in the backup are deleted;
5. starts everything, applies the migrations (as `make migrate` does, so a backup from an older version is brought up to date) and checks that `GET /api/v1/ready` answers 200.

**Restoring on a new server:** install the requirements, clone the same Nightloom version (the `version` and `git_commit` in `manifest.json`) or a newer one, put the old `.env` back (`chmod 600 .env`) with `NIGHTLOOM_VERSION` set to that version, run `make deploy-pull`, then `make restore BACKUP=...`. The stack does not need to be running first. A backup cannot be restored into an *older* Nightloom version.

## Upgrades

Read the [release notes](https://github.com/TheRealNightmare/nightloom/releases) of every version between yours and the new one first: a new major version of PostgreSQL or Neo4j needs a backup and restore, and the notes will say so. Then, for example to 0.2.0:

```sh
make backup                                   # always back up first
git fetch --tags && git checkout v0.2.0       # the Compose files of the new release
sed -i 's/^NIGHTLOOM_VERSION=.*/NIGHTLOOM_VERSION=0.2.0/' .env
make deploy-pull                              # download the new images
make up                                       # docker compose ... up -d: recreates what changed
```

The one-shot `migrate` service applies the database migrations and the Neo4j schema before `api` and `worker` start, so no separate step is needed. Then check that the API is ready and runs the new version:

```sh
make ps                                       # every service healthy, migrate "exited (0)"
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml --env-file .env \
  exec api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/api/v1/ready').read().decode()); print(u.urlopen('http://127.0.0.1:8000/api/v1/health').read().decode())"
```

`/api/v1/ready` must answer `200` and `/api/v1/health` show the new `version`; the account menu in the web UI shows it too. To go back, restore the backup you made with the old version's checkout and `NIGHTLOOM_VERSION`. Pinned third-party images (Caddy, cloudflared, the databases) only change with a Nightloom release.

**From 0.1.0 to 0.2.0**: the steps above are all. `migrate` applies migrations `0007` to `0012` (graph layouts, saved views, the result cache, scan profiles, auto-pivot, ownership verification). The result cache works with its defaults; `NIGHTLOOM_RESULT_CACHE_SCOPE`, `NIGHTLOOM_RESULT_CACHE_MAX_BYTES` and `NIGHTLOOM_RESULT_CACHE_MAX_ENTRY_BYTES` are optional (see `.env.example`). The two active Shuttles connect from the worker to the targets a case has verified (HTTPS, HTTP and 20 common TCP ports): an outbound firewall on the host decides whether they can.

**From a version before 0.1.0** (built on the server): add `NIGHTLOOM_VERSION=0.1.0` to `.env`, then follow the steps above. The old locally built `nightloom-api:dev` and `nightloom-web:dev` images are no longer used; `docker image rm nightloom-api:dev nightloom-web:dev` frees their space.

### Running an unreleased version

The production stack can also build the images from the checkout, for example to try a fix before it is released. Add `FROM_SOURCE=1` to every `make` command (`make up FROM_SOURCE=1`, `make backup FROM_SOURCE=1`, ...); it adds [`deploy/compose.source.yaml`](../deploy/compose.source.yaml). CI tests the production configuration this way. Go back to a release as soon as there is one.

## Small hosts and Raspberry Pi

A **Raspberry Pi 4B with 8 GB** runs Nightloom for a small team, with these settings:

- a **64-bit** OS (Raspberry Pi OS 64-bit, Debian or Ubuntu for arm64). Neo4j and Node.js publish no 32-bit ARM images, so a 32-bit OS cannot run the stack;
- an **SSD over USB 3**, not the SD card, for Docker's data (`/var/lib/docker`). Databases wear out SD cards quickly and are slow on them;
- the small-host overrides ([`deploy/compose.small.yaml`](../deploy/compose.small.yaml): lower Neo4j memory, one worker process, lower limits). Use `make secrets ARGS="--small ..."`, or set `NIGHTLOOM_SMALL_HOST=true` in `.env`, and `make` adds the file. The raw command is `docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml -f deploy/compose.small.yaml --env-file .env up -d`;
- the release images include `linux/arm64`, so nothing is compiled on the Pi; `make deploy-pull` downloads them;
- Cloudflare Tunnel is often the easier edge on a home network (no port forwarding).

Every image the stack uses publishes `linux/arm64`:

| Image | Used for | arm64 |
| --- | --- | --- |
| `postgres:18.7-alpine` | PostgreSQL | yes |
| `neo4j:2026.10.2-community` | Neo4j | yes (no 32-bit ARM) |
| `valkey/valkey:9.0.7-alpine` | Valkey | yes |
| `chrislusf/seaweedfs:4.54` | object storage | yes |
| `caddy:2.11.9-alpine` | HTTPS edge (`https` profile) | yes |
| `cloudflare/cloudflared:2026.11.1` | tunnel edge (`tunnel` profile) | yes (amd64 and arm64 only) |
| `rclone/rclone:1.75.2` | bucket copy for backup and restore | yes |
| `ghcr.io/therealnightmare/nightloom-backend:<version>` | API, worker and migrations | yes (amd64 and arm64 only) |
| `ghcr.io/therealnightmare/nightloom-web:<version>` | web UI (nginx) | yes (amd64 and arm64 only) |
| `python:3.12.16-slim`, `node:26.13.1-alpine3.24`, `nginxinc/nginx-unprivileged:1.30.6-alpine3.24` | base images of the Nightloom images (only pulled when building from source) | yes (Node.js: no 32-bit ARM) |

## Day-to-day operation

```sh
make ps                     # containers and their health
make logs SERVICE=api       # follow one service's logs (caddy, worker, ...)
docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml --env-file .env \
  exec api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/api/v1/ready').read().decode())"
```

The services restart automatically after a crash or a reboot (`restart: unless-stopped`). `make down` stops the stack and keeps the data. `make reset` deletes **all** data (it asks first).

## Production checklist

- [ ] **Firewall:** inbound SSH (preferably limited to your addresses) and, with Caddy, TCP 80/443 and UDP 443. Nothing else. With Cloudflare Tunnel, no inbound ports at all except SSH. Docker publishes ports past `ufw`/`firewalld` rules. In production only Caddy publishes any, and only the ones it needs.
- [ ] **Secrets:** `.env` was made by `make secrets`, has mode `600` and has a copy in a safe place. `NIGHTLOOM_ENV=production` is set.
- [ ] **Release:** the checkout is a release tag and `NIGHTLOOM_VERSION` in `.env` is that release. The Nightloom images can be checked with `gh attestation verify oci://ghcr.io/therealnightmare/nightloom-backend:<version> --repo TheRealNightmare/nightloom` (and the same for `nightloom-web`).
- [ ] **HTTPS works:** the browser shows a valid certificate, `http://` redirects to `https://`, and the response has `Strict-Transport-Security` (Caddy) or HSTS is enabled in Cloudflare (tunnel).
- [ ] **OS and Docker updates:** automatic security updates are on (for example `unattended-upgrades` on Debian and Ubuntu), and Docker Engine is kept current.
- [ ] **Backups:** a nightly `make backup` cron job runs, the archives are **copied off the machine** (encrypted), and a test restore has succeeded at least once.
- [ ] **Accounts:** the first admin has a strong, unique password, and every user has their own account with the lowest role that fits (viewer, analyst or admin).
- [ ] **Ethics:** every user has read [ETHICS.md](../ETHICS.md). Cases must state a purpose and a legal basis, and data collection is for lawful investigation of public information only.
- [ ] **Licences of data sources:** some Shuttles have terms of their own. For example, Shodan InternetDB is free for non-commercial use only (see [Available Shuttles](../README.md#available-shuttles)).
- [ ] **Never on a server:** `deploy/compose.test.yaml` and `deploy/compose.edge-test.yaml` (test-only DNS server, Caddy's internal CA, published database port).

Report security problems privately, as described in [SECURITY.md](../SECURITY.md).
