# Nightloom

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/TheRealNightmare/nightloom/actions/workflows/ci.yml/badge.svg)](https://github.com/TheRealNightmare/nightloom/actions/workflows/ci.yml)

**Every thread, woven into the full picture.**

Nightloom is an open-source, self-hosted OSINT tool that collects **public** information and maps it as an interactive investigation graph. Start from a domain, IP address, organization or other entity, let Nightloom pivot through public data sources, and explore how everything connects — with provenance, evidence and an audit trail for every finding.

> 🚧 **Status: early development.** Nothing below is implemented yet. This README describes what is **planned**. See the [roadmap](docs/ROADMAP.md) for progress.

## Preview

The web UI is a skeleton for now: a graph canvas with a built-in **demo investigation** (fictional `example.org` data, clearly labelled "Demo data"), a details and provenance panel, an entity-type legend, search, and a live API status indicator. There is no login and no real data yet. A screenshot will be added here once the UI settles.

## Quick start (developers)

So far there is an API with authentication and user management, the local infrastructure and the web UI skeleton. With [uv](https://docs.astral.sh/uv/), [Node.js](https://nodejs.org/) 26 LTS (with [Corepack](https://github.com/nodejs/corepack) for pnpm) and `make` installed:

```sh
make install       # install the Python dependencies
make web-install   # install the web dependencies (pnpm)
make deps-up       # start PostgreSQL and Valkey (Docker), needed by the tests
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
