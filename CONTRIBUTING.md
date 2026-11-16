# Contributing to Nightloom

Thanks for your interest in Nightloom. The project is in early development, so the codebase is still taking shape — issues, design feedback and documentation fixes are especially welcome.

Before contributing, read the [Code of Conduct](CODE_OF_CONDUCT.md) and [ETHICS.md](ETHICS.md). Contributions that enable prohibited uses will not be accepted.

## Workflow

1. Open or find an issue describing the change (small fixes can skip this).
2. Fork the repository, or create a branch if you have write access. Name it after the change, e.g. `feat/dns-shuttle` or `docs/fix-typo`.
3. Make your changes in small, focused commits.
4. Open a pull request against `main` and fill in the template.

`main` is protected: **no direct pushes**. Every change goes through a pull request.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(optional scope): <short summary>
```

| Type | Use for |
| --- | --- |
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `chore` | Maintenance, tooling, repo hygiene |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or updating tests |
| `ci` | CI configuration |
| `build` | Build system or dependencies |

Example: `feat(shuttles): add passive DNS Shuttle`

## Developer Certificate of Origin (DCO)

Every commit must be signed off to certify that you have the right to submit it under the project's license. Sign off with `-s`:

```sh
git commit -s -m "fix: handle empty DNS answers"
```

This adds a line like the following to the commit message:

```text
Signed-off-by: Your Name <your.address@example.com>
```

By signing off you agree to the [Developer Certificate of Origin 1.1](https://developercertificate.org/). In summary, you certify that:

1. The contribution was created in whole or in part by you and you have the right to submit it under the project's open-source license; or
2. It is based on previous work that, to the best of your knowledge, is covered by a compatible open-source license and you have the right to submit it with modifications; or
3. It was provided to you by someone who certified (1) or (2), and you have not modified it; and
4. You understand that the project and your contribution are public, and that a record of the contribution (including your sign-off) is kept indefinitely and may be redistributed.

Forgot to sign off? Amend the last commit with `git commit --amend -s --no-edit`, or sign off a range with `git rebase --signoff main`.

## Development setup

The Python code lives in a [uv](https://docs.astral.sh/uv/) workspace (Python 3.12). You need `uv` and `make`; uv downloads the right Python version for you.

1. Install uv by following the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
2. Install the workspace packages and dev tools:

   ```sh
   make install
   ```

3. Run the linters, type checker and tests (the same checks a pull request must pass):

   ```sh
   make check
   ```

4. Start the API dev server (auto-reloads on changes):

   ```sh
   make api
   ```

   Then open the interactive API docs at <http://127.0.0.1:8000/api/docs>.

5. Install the Git pre-commit hook so formatting, linting and type checks run before each commit:

   ```sh
   uv run pre-commit install
   ```

Run `make` (or `make help`) to list all targets; `make fmt` formats code and applies lint fixes.

## Running the stack

[`deploy/compose.yaml`](deploy/compose.yaml) runs the API together with its infrastructure. It needs [Docker](https://docs.docker.com/get-started/get-docker/) with the Compose plugin and about 1.5 GB of free RAM (Neo4j uses most of it).

| Service | Image | Role |
| --- | --- | --- |
| `api` | built from `apps/api/Dockerfile` | Nightloom API |
| `migrate` | same image as `api` | One-shot: applies database migrations, then exits |
| `postgres` | PostgreSQL 18 | Relational store |
| `neo4j` | Neo4j Community Edition | Graph store |
| `valkey` | Valkey (Redis-compatible) | Queue broker and cache |
| `seaweedfs` | SeaweedFS | S3-compatible object storage |

1. Create your local configuration and **change every `change-me-...` value**:

   ```sh
   cp .env.example .env
   ```

   `.env` is git-ignored. The connection URLs in it (`NIGHTLOOM_DATABASE_URL`, `NIGHTLOOM_VALKEY_URL`, ...) repeat the passwords, so keep them in sync. If `.env` is missing, `make up` copies `.env.example` for you.

2. Build and start everything, waiting until all services are healthy:

   ```sh
   make up
   ```

3. Check the services:

   ```sh
   make ps                 # status and health
   make logs SERVICE=api   # follow one service's logs (omit SERVICE for all)
   ```

Every port is published on `127.0.0.1` only:

| What | URL |
| --- | --- |
| API docs | <http://127.0.0.1:8000/api/docs> |
| Neo4j Browser | <http://127.0.0.1:7474> (user `neo4j`, password `NEO4J_PASSWORD`; Bolt on `127.0.0.1:7687`) |
| S3 endpoint | <http://127.0.0.1:8333> (keys `NIGHTLOOM_S3_ACCESS_KEY` / `NIGHTLOOM_S3_SECRET_KEY`, bucket `NIGHTLOOM_S3_BUCKET`) |
| PostgreSQL | `127.0.0.1:5432` |
| Valkey | `127.0.0.1:6379` |

If one of these ports is already taken on your machine (for example by a local PostgreSQL), set the matching `*_HOST_PORT` variable in `.env`, e.g. `POSTGRES_HOST_PORT=5433`. Containers still reach each other on the default ports.

`make down` stops the stack and keeps your data in Docker volumes. Database passwords are only applied when a volume is first created, so changing them in `.env` afterwards requires a reset.

> [!WARNING]
> `make reset` **permanently deletes all local data** (PostgreSQL, Neo4j, Valkey and object storage volumes). It asks you to type `yes` first.

Run `make compose-config` to validate the Compose file after editing it.

`make ps` lists `migrate` as `Exited (0)` once the migrations have been applied; that is expected. Check that the API can reach every dependency with:

```sh
curl http://127.0.0.1:8000/api/v1/ready
```

### Database migrations

The schema is managed by Alembic ([`apps/api/alembic/`](apps/api/alembic/)). `make up` applies all migrations before the API starts.

```sh
make migrate                       # apply pending migrations to the running stack
make revision m="add cases table"  # autogenerate a new revision from the ORM models
```

`make revision` runs Alembic on your machine against the stack's PostgreSQL on `127.0.0.1`, so run `make up` first. The database must already be at the latest revision. Import new model modules in `apps/api/alembic/env.py` so autogenerate sees them. Always review the generated file in `apps/api/alembic/versions/` before committing it: autogenerate does not detect everything (e.g. renames).

### Integration tests

`make test` runs only the unit tests, which need no services. Tests marked `@pytest.mark.integration` (in `apps/api/tests/integration/`) talk to the real stack and are skipped by default:

```sh
make up
make test-integration
```

`make test-integration` reads the credentials from `.env` and connects through the ports published on `127.0.0.1` (including any `*_HOST_PORT` overrides). The passwords are inserted into connection URLs as they are, so keep them free of URL-special characters such as `@`, `:`, `/` or `%`.

## Continuous integration

Every pull request and every push to `main` runs the [CI workflow](.github/workflows/ci.yml) on GitHub Actions:

| Job | What it checks |
| --- | --- |
| `lint` | All pre-commit hooks except mypy (Ruff lint and format, YAML/TOML checks, whitespace) |
| `typecheck` | `make typecheck` (mypy, strict) |
| `test` | `make test` (unit tests, at least 90% coverage); the coverage report is uploaded as an artifact |
| `docker` | `deploy/compose.yaml` is valid, the API image builds, and the container started on its own answers `/api/v1/health` with 200 and `/api/v1/ready` with 503 |
| `integration` | Starts the full stack with `make up` and runs `make test-integration`; dumps the container logs on failure |
| `dco` | Pull requests only: every non-merge commit has a `Signed-off-by:` trailer (see [DCO](#developer-certificate-of-origin-dco)) |

Each job installs dependencies with `uv sync --locked`, so CI fails if `uv.lock` is out of date. Run `uv lock` and commit the result whenever you change dependencies.

A pull request can only be merged when all jobs are green and every commit is signed off. Run `make check` locally before pushing to catch most failures early.

Coming later: [pnpm](https://pnpm.io/) for the web frontend.

## Writing a Shuttle

Shuttles are Nightloom's plugins: each one queries a single data source and returns entities and relationships. The Shuttle SDK (`packages/sdk`) is coming soon; the planned contract is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#shuttle-contract).

To propose a new data source now, open a **Shuttle request** issue.
