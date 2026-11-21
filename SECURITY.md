# Security Policy

## Supported versions

Nightloom is in early development (0.x). Security fixes land on `main` and ship in the next release; only the latest release gets them, so upgrade to it ([Upgrades](docs/DEPLOYMENT.md#upgrades)).

| Version | Supported |
| --- | --- |
| Latest release (0.2.x) | ✅ |
| `main` branch | ✅ |
| Older releases (0.1.x) | ❌ |

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Report privately through GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, affected component or commit, steps to reproduce and potential impact.

Direct link: <https://github.com/TheRealNightmare/nightloom/security/advisories/new>

The maintainers will acknowledge your report, investigate, and coordinate a fix and disclosure with you. Please give us reasonable time to address the issue before any public disclosure.

## Security design

How authentication works (server-side sessions, CSRF protection, password hashing, roles and sign-in rate limits) is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#authentication). Reading it first makes a report easier to write and to triage.

## Deploying securely

Run production instances as described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): `make secrets` for the secrets, HTTPS through Caddy or Cloudflare Tunnel, no database or API ports published, nightly backups copied off the machine, and the production checklist at the end of that guide. Never use the test-only Compose files (`deploy/compose.test.yaml`, `deploy/compose.edge-test.yaml`) on a server.

## Scope

In scope: vulnerabilities in Nightloom's own code and default configuration — for example authentication or authorization bypass, exposure of users' stored API keys, injection (including Cypher injection), or a guardrail from [ETHICS.md](ETHICS.md) that can be bypassed.

Out of scope: vulnerabilities in third-party data sources or services that Shuttles query; report those to their owners.
