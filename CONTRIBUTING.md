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

3. Start PostgreSQL, Valkey and Neo4j (the tests need them; this needs [Docker](https://docs.docker.com/get-started/get-docker/), see [Tests](#tests)), then run the linters, type checker and tests (the same checks a pull request must pass):

   ```sh
   make deps-up
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

### Web frontend

The UI lives in [`apps/web`](apps/web/) (React, Vite, TypeScript, [Sigma.js](https://www.sigmajs.org/) with [graphology](https://graphology.github.io/)). It is a [pnpm](https://pnpm.io/) workspace rooted at the repository root, so `pnpm-lock.yaml` sits next to `uv.lock`.

1. Install [Node.js](https://nodejs.org/) 26 (the current Active LTS; [`.node-version`](.node-version) has the major version, which most version managers read). Then enable [Corepack](https://github.com/nodejs/corepack), which provides the exact pnpm version pinned in `package.json`. Node 25 and later no longer ship Corepack, so install it first:

   ```sh
   npm install --global corepack
   corepack enable
   ```

2. Install the locked dependencies:

   ```sh
   make web-install
   ```

3. Start the API (`make api`, or the whole stack with `make up`) and, in a second terminal, the web dev server:

   ```sh
   make web-dev
   ```

   Open <http://127.0.0.1:5173> and sign in (create a user with `make create-admin` first; see [First admin](#first-admin)). The dev server proxies `/api` to `http://127.0.0.1:8000`; if you changed `API_HOST_PORT`, set `NIGHTLOOM_API_PROXY_TARGET` (e.g. `NIGHTLOOM_API_PROXY_TARGET=http://127.0.0.1:8001 make web-dev`). Without a reachable API the UI stays on the sign-in screen and reports that it cannot reach Nightloom.

4. Check your changes (the same checks CI runs):

   ```sh
   make check-web   # ESLint + Prettier, tsc, Vitest with coverage (≥ 80%)
   make web-build   # production build into apps/web/dist
   make check-all   # Python and web checks together
   ```

   `make web-fmt` formats the code and applies ESLint fixes. The pre-commit hook runs Prettier and ESLint whenever files under `apps/web/` are staged.

The graph shows a built-in demo fixture ([`src/demo/sampleGraph.ts`](apps/web/src/demo/sampleGraph.ts)). Keep it fictional: only `example.org` names, documentation IP ranges (RFC 5737) and AS numbers (RFC 5398), and no person entities. Its tests enforce this. Keep graph logic in pure functions ([`src/graph/view.ts`](apps/web/src/graph/view.ts)) so it can be tested without WebGL; component tests replace sigma with a stand-in.

#### End-to-end tests

[Playwright](https://playwright.dev/) tests in [`apps/web/e2e/`](apps/web/e2e/) drive a real Chromium against the running stack: signing in with a wrong and a right password, reloading, signing out, and checking that the session cookie is `HttpOnly`. Install the browser once, start the stack, then run them with a throwaway account of your choice:

```sh
pnpm -C apps/web exec playwright install chromium   # once (add --with-deps on a fresh Linux machine)
make up
export NIGHTLOOM_E2E_EMAIL=e2e-admin@example.org
export NIGHTLOOM_E2E_PASSWORD='a-long-local-only-password'   # 12-128 characters
make e2e
```

`make e2e` creates that account as an **admin in your local stack** on the first run (with the CLI's `--password-stdin`) and reuses it afterwards, so keep the same password; if you change it, delete the user or run `make reset`. The tests use the web container on `127.0.0.1:${WEB_HOST_PORT:-8080}`. Never use real credentials here. A failed run leaves a report in `apps/web/playwright-report/` (`pnpm -C apps/web exec playwright show-report`).

Add dependencies with `pnpm --dir apps/web add <package>` (or `add -D` for tooling) and commit the updated `pnpm-lock.yaml`.

## Running the stack

[`deploy/compose.yaml`](deploy/compose.yaml) runs the API together with its infrastructure. It needs [Docker](https://docs.docker.com/get-started/get-docker/) with the Compose plugin and about 1.5 GB of free RAM (Neo4j uses most of it).

| Service | Image | Role |
| --- | --- | --- |
| `web` | built from `apps/web/Dockerfile` | Web UI (nginx), proxies `/api/` to `api` |
| `api` | built from `apps/api/Dockerfile` | Nightloom API |
| `worker` | same image as `api` | Runs Shuttle jobs from the Valkey queue (Celery, 4 processes by default: `NIGHTLOOM_WORKER_CONCURRENCY`) |
| `migrate` | same image as `api` | One-shot: applies the PostgreSQL migrations and the Neo4j schema, then exits |
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
| Web UI | <http://127.0.0.1:8080> (the API is also reachable through it under `/api/`) |
| API docs | <http://127.0.0.1:8000/api/docs> |
| Neo4j Browser | <http://127.0.0.1:7474> (user `neo4j`, password `NEO4J_PASSWORD`; Bolt on `127.0.0.1:7687`) |
| S3 endpoint | <http://127.0.0.1:8333> (keys `NIGHTLOOM_S3_ACCESS_KEY` / `NIGHTLOOM_S3_SECRET_KEY`, bucket `NIGHTLOOM_S3_BUCKET`) |
| PostgreSQL | `127.0.0.1:5432` |
| Valkey | `127.0.0.1:6379` |

If one of these ports is already taken on your machine (for example by a local PostgreSQL), set the matching `*_HOST_PORT` variable in `.env`, e.g. `POSTGRES_HOST_PORT=5433`. Containers still reach each other on the default ports.

### First admin

There is no public sign-up. With the stack running, create the first admin; the command prompts for the password twice (12–128 characters) and fails if the email is already taken:

```sh
make create-admin email=you@example.org name="Your Name"
```

It runs `python -m nightloom_api.cli create-admin` in a one-off `api` container. Then sign in at <http://127.0.0.1:8080> (or through the API with `POST /api/v1/auth/login`); admins then create everyone else through `/api/v1/users`. To pipe the password in instead (for scripts), call the CLI with `--password-stdin`. See [Authentication](docs/ARCHITECTURE.md#authentication) for the design.

`make down` stops the stack and keeps your data in Docker volumes. Database passwords are only applied when a volume is first created, so changing them in `.env` afterwards requires a reset.

> [!WARNING]
> `make reset` **permanently deletes all local data** (PostgreSQL, Neo4j, Valkey and object storage volumes). It asks you to type `yes` first.

Run `make compose-config` to validate the Compose file after editing it.

`make ps` lists `migrate` as `Exited (0)` once the migrations and the Neo4j schema have been applied; that is expected. Check that the API can reach every dependency with:

```sh
curl http://127.0.0.1:8000/api/v1/ready
```

### Database migrations

The PostgreSQL schema is managed by Alembic ([`apps/api/alembic/`](apps/api/alembic/)). The Neo4j schema (constraints and indexes for entity nodes) is applied by `python -m nightloom_api.graph.schema apply`, which is idempotent; `python -m nightloom_api.graph.schema show` lists it. `make up` applies both before the API starts.

```sh
make migrate                       # apply pending migrations and the Neo4j schema to the running stack
make revision m="add cases table"  # autogenerate a new revision from the ORM models
```

`make revision` runs Alembic on your machine against the stack's PostgreSQL on `127.0.0.1`, so run `make up` first. The database must already be at the latest revision. Import new model modules in `apps/api/alembic/env.py` so autogenerate sees them. Always review the generated file in `apps/api/alembic/versions/` before committing it: autogenerate does not detect everything (e.g. renames).

### Tests

The tests live in `apps/api/tests/`, `apps/worker/tests/` (the job runner, against the same databases as the API's tests), `packages/core/tests/` (the shared entity model: table-driven normalizer tests and [Hypothesis](https://hypothesis.readthedocs.io/) property tests, which need no services), `packages/sdk/tests/` (the Shuttle SDK, including the SSRF guard; no services, no internet) and `shuttles/*/tests/` (the built-in Shuttles). They come in five kinds:

| Kind | Marker | Needs | Run with |
| --- | --- | --- | --- |
| Unit | none | nothing | `make test-unit` (quick, no coverage gate); `make test-core-compat` runs the `packages/core` tests on every Python in `CORE_COMPAT_PYTHONS` (3.12.0, the latest 3.12 and 3.13, installed by uv) |
| Database | `@pytest.mark.db` (in `apps/api/tests/db/` and `apps/worker/tests/`) | PostgreSQL, Valkey and Neo4j | `make test` |
| Graph | `@pytest.mark.graph` (in `apps/api/tests/db/` and `apps/worker/tests/`) | Neo4j | `make test` |
| Integration | `@pytest.mark.integration` (in `apps/api/tests/integration/`) | the whole stack | `make test-integration` (`TEST_DNS=1` adds the DNS job test, below) |
| Network | `@pytest.mark.network` | the internet (public DNS, crt.sh, RDAP, RIPEstat) | `make test-network`; never in CI |

`make test` runs the unit, database **and** graph tests with a coverage gate of 90% (over `nightloom_api`, `nightloom_core`, `nightloom_sdk`, `nightloom_worker` and the built-in Shuttles), so start the services first. The database fixtures live in `apps/api/tests/db_fixtures.py`; both the API's and the worker's `conftest.py` import them, and the API tests replace the Celery queue with a recorder, so no test ever needs a running worker. `make deps-up` starts only PostgreSQL, Valkey and Neo4j from the Compose file (`make up` works too):

```sh
make deps-up
make test
```

The database tests never touch your development data: they use a separate `nightloom_test` database, created on first use, and Valkey database 15. Migrations are applied once per test run; every test starts with empty tables and an empty Valkey database. `make test` builds both URLs from `.env` (or `.env.example`) and your `*_HOST_PORT` overrides; set `NIGHTLOOM_TEST_DATABASE_URL` and `NIGHTLOOM_TEST_VALKEY_URL` to point them elsewhere (as CI does). As a safety net the tests refuse a database whose name does not contain `test` and Valkey database 0. Neo4j Community Edition has a single database, so the graph tests use the stack's Neo4j (`NIGHTLOOM_TEST_NEO4J_URI` and `NIGHTLOOM_TEST_NEO4J_PASSWORD`, derived from `.env`); they only create and delete graphs of their own random case IDs, and every graph query is scoped to one case, so your cases are never touched. Code that depends on the time (session expiry) reads it from `nightloom_api.auth.clock.utcnow`, which tests replace to move the clock.

Integration tests talk to the real stack and are skipped unless you ask for them:

```sh
make up
make test-integration
```

The DNS job test needs the stack's **test-only DNS server** ([`deploy/compose.test.yaml`](deploy/compose.test.yaml): CoreDNS, authoritative for `nightloom-test.example` only, with the worker's DNS Shuttle pointed at it through `NIGHTLOOM_DNS_ALLOW_PRIVATE_RESOLVERS=true`), so it never touches the internet. CI runs it this way:

```sh
make up TEST_DNS=1
make test-integration TEST_DNS=1
make down TEST_DNS=1
```

`make test-integration` reads the credentials from `.env` and connects through the ports published on `127.0.0.1` (including any `*_HOST_PORT` overrides). The passwords are inserted into connection URLs as they are, so keep them free of URL-special characters such as `@`, `:`, `/` or `%`.

## Continuous integration

Every pull request and every push to `main` runs the [CI workflow](.github/workflows/ci.yml) on GitHub Actions:

| Job | What it checks |
| --- | --- |
| `lint` | All pre-commit hooks except mypy and the web hooks (Ruff lint and format, YAML/TOML checks, whitespace) |
| `typecheck` | `make typecheck` (mypy, strict) |
| `core-compat` | `make test-core-compat` for one Python each (3.12.0, latest 3.12, 3.13): the `packages/core` tests, so canonical keys stay identical across interpreters |
| `test` | `make test` (unit, database and graph tests of the API, worker, core, SDK and built-in Shuttles, at least 90% coverage) against PostgreSQL, Valkey and Neo4j service containers; the coverage report is uploaded as an artifact |
| `web` | `pnpm install --frozen-lockfile`, then `make web-lint`, `web-typecheck`, `web-test` (at least 80% coverage; the report is uploaded as an artifact) and `web-build` |
| `docker` | `deploy/compose.yaml` (alone and with `deploy/compose.test.yaml`) is valid; the API image builds and, started on its own, answers `/api/v1/health` with 200 and `/api/v1/ready` with 503; it ships Celery and the built-in Shuttles (`crtsh`, `dns`, `rdap`, `registrable-domain`, `ripestat`; the worker uses the same image), their configuration check passes with the defaults and refuses a private DNS resolver; the web image builds and serves `/` as HTML |
| `integration` | Starts the full stack with `make up TEST_DNS=1` (adds the test-only CoreDNS server), runs `make test-integration TEST_DNS=1` (including a `registrable-domain` job and a `dns` job against the test zone, end to end through the API, Valkey, the worker and Neo4j), checks `/api/v1/health` through the web container's proxy, then installs Playwright's Chromium and runs `make e2e` with throwaway CI-only credentials (the DNS Shuttle test uses the test zone); dumps the container logs and uploads the Playwright report on failure |
| `dco` | Pull requests only: every non-merge commit has a `Signed-off-by:` trailer (see [DCO](#developer-certificate-of-origin-dco)) |

The Python jobs install dependencies with `uv sync --locked` and the web job with `pnpm install --frozen-lockfile`, so CI fails if `uv.lock` or `pnpm-lock.yaml` is out of date. Commit the updated lockfile whenever you change dependencies.

A pull request can only be merged when all jobs are green and every commit is signed off. Run `make check-all` locally before pushing to catch most failures early.

## Writing a Shuttle

Shuttles are Nightloom's plugins: each one takes **one entity** and yields **findings** (entities and relationships), usually from a single data source. They are ordinary Python packages that depend on the Shuttle SDK ([`packages/sdk`](packages/sdk/), import `nightloom_sdk`) and register under an entry point. The worker runs them; they never write to the graph, the database or the network on their own terms. The full contract, including every check the worker makes, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#shuttle-contract).

To propose a new data source without writing it yourself, open a **Shuttle request** issue.

### Built-in Shuttles

| Shuttle | Package | What it does |
| --- | --- | --- |
| `registrable-domain` | [`shuttles/registrable-domain`](shuttles/registrable-domain/) | Domain → its registrable domain, from the bundled Public Suffix List (no network). The smallest complete example. |
| `dns` | [`shuttles/dns`](shuttles/dns/) | Domain → its public DNS records, through public resolvers only. The example for a non-HTTP protocol behind a fake client. |
| `crtsh` | [`shuttles/crtsh`](shuttles/crtsh/) | Domain → subdomains and certificates from Certificate Transparency via crt.sh. The example for an HTTP source through `ctx.http`: retries, a response that is too large, job notes and fixed failure messages. |
| `rdap` | [`shuttles/rdap`](shuttles/rdap/) | Domain, IP address, netblock or ASN → registration data (RDAP) from the responsible registry. The example for several input types, a per-process cache shared by jobs (IANA's bootstrap files), `Retry-After`, and keeping person data out of findings in one tested function. |
| `ripestat` | [`shuttles/ripestat`](shuttles/ripestat/) | IP address, netblock or ASN → announced prefixes, origin ASes and AS holders from the RIPEstat Data API. The example for validating every answer with Pydantic models, counting malformed items in a job note, and a shared SDK helper (`nightloom_sdk.redaction`). |

### A minimal Shuttle

The built-in [`shuttles/registrable-domain`](shuttles/registrable-domain/) is a complete, small example. Its shape:

```python
from collections.abc import AsyncIterator

from nightloom_core.entities import EntityType, Sensitivity
from nightloom_core.relationships import RelationType
from nightloom_sdk import (
    EntityFinding,
    EntityInput,
    EntityRef,
    Finding,
    Mode,
    RelationshipFinding,
    Shuttle,
    ShuttleContext,
    ShuttleMeta,
)


class ParentDomain(Shuttle):
    meta = ShuttleMeta(
        name="parent-domain",  # lowercase slug, also the entry point's name
        version="1.0.0",  # semver; becomes the source "shuttle:parent-domain@1.0.0"
        title="Parent domain",
        description="Links a subdomain to its parent domain.",
        input_types=frozenset({EntityType.DOMAIN}),
        output_entity_types=frozenset({EntityType.DOMAIN}),
        output_relationship_types=frozenset({RelationType.HAS_SUBDOMAIN}),
        mode=Mode.PASSIVE,  # active Shuttles are refused for now
        sensitivity=Sensitivity.INFRASTRUCTURE,
        network=False,  # True needs a rate_limit and gives you ctx.http
        default_admiralty="C3",  # reliability A-F, credibility 1-6
        default_confidence=0.6,
    )

    async def run(self, ctx: ShuttleContext, entity: EntityInput) -> AsyncIterator[Finding]:
        parent = entity.value.partition(".")[2]
        if parent.count(".") < 1:
            return
        yield EntityFinding(type=EntityType.DOMAIN, value=parent)
        yield RelationshipFinding(
            type=RelationType.HAS_SUBDOMAIN,
            source=EntityRef(type=EntityType.DOMAIN, value=parent),
            target=entity.ref,
        )
```

Register it in the package's `pyproject.toml`. The entry point's name must equal `meta.name`:

```toml
[project.entry-points."nightloom.shuttles"]
parent-domain = "nightloom_shuttle_parent_domain:ParentDomain"
```

`make shuttles` (`python -m nightloom_sdk.registry list`) shows what is installed. Discovery is strict: a duplicate name, an entry point that does not load, or invalid metadata is an error, not a silent skip.

### Rules the SDK and the worker enforce

- **Metadata is validated** when the class is defined and again when it is loaded: the relationship types must fit the [allow-list](docs/ARCHITECTURE.md#relationships) for your input and output entity types; `sensitivity` must be at least that of every type you take or produce (any person-related type makes it a `person` Shuttle, which only runs in cases that allow person entities); `timeout_seconds` is 1–600 (default 60).
- **Yield raw values.** Do not normalize: the worker normalizes every value with `nightloom_core`, then checks it against your declared output types, the case's person gate and the allow-list. A finding that fails is counted as rejected (with a reason, never the value) and not stored. A relationship's endpoints must exist when it arrives, so yield an entity before the relationships that use it.
- **Network access only through `ctx.http`**, a guarded `httpx.AsyncClient`: it refuses private, loopback, link-local, multicast and reserved destinations after DNS resolution (and connects to the address it checked), allows only `http`/`https` (redirects too), applies your `rate_limit` per host, caps responses at 5 MB and identifies itself as `Nightloom/<version> (+https://github.com/TheRealNightmare/nightloom)`. Do not create your own clients or sockets. Respect the source's terms (link them in `terms_url`) and `robots.txt`. The one exception so far is the [DNS Shuttle](docs/ARCHITECTURE.md#the-dns-shuttle), which speaks DNS, not HTTP: it talks only to configured resolvers that must be public addresses, and applies `rate_limit` per resolver with the SDK's `HostRateLimiter`. Another non-HTTP protocol needs the same kind of guard; discuss it in an issue first.
- **Refuse inputs early with a fixed reason.** If your Shuttle must not handle an input (the DNS Shuttle refuses internal-only names, the crt.sh Shuttle public suffixes such as `co.uk`), raise `InputRefusedError(RefusalReason.…)` before doing any work: the job fails with that reason's fixed message. Add a reason (and its message) to the SDK rather than raising free text.
- **Fail clearly when the source fails.** When a run cannot finish for a reason the user can act on (the source is down after your retries, its answer is unreadable or too large), raise `ShuttleFailedError(FailureReason.…)`: the job fails with that reason's fixed, user-safe message instead of the generic one, and keeps what was stored. Again, add a reason to the SDK rather than raising free text.
- **Say what you narrowed.** `ctx.note("...")` keeps a short remark with the job (at most 10, of 200 characters), shown in the *Jobs* panel: that a result was capped, that expired certificates were left out, how many names were dropped and why. Write fixed text and counts, never values you found, responses or secrets. Notes are not copied into the audit log.
- **Check your settings at startup.** A Shuttle configured through environment variables overrides `check_configuration()` and raises `ShuttleConfigurationError` with a message that names the setting; the worker refuses to start if it fails (`python -m nightloom_sdk.registry check` runs it too).
- **No secrets in logs or findings.** Use `ctx.logger` and never log keys, tokens or whole responses. API keys you declare in `required_keys` arrive read-only in `ctx.keys` (user-supplied keys come in a later release; until then the mapping is empty).
- **Stop when asked.** The worker stops between findings once a job is cancelled; in a long loop, check `ctx.cancelled` (or call `ctx.raise_if_cancelled()`) before expensive work. `ctx.progress("...")` logs a short status line.
- **Other errors** end the job as `failed` with a generic message; only the exception's type is logged. More than 5,000 findings, or running past `timeout_seconds`, also stops the job (findings already stored are kept).

### Testing a Shuttle

`nightloom_sdk.testing` runs a Shuttle without the worker, the databases or the internet:

```python
import httpx
from nightloom_core.entities import EntityType
from nightloom_sdk.testing import assert_declared, collect, entity_input, fake_context


async def test_parent_domain() -> None:
    entity = entity_input(
        EntityType.DOMAIN, "WWW.Example.org"
    )  # normalized like the case stores it
    findings = await collect(ParentDomain(), entity)
    assert_declared(ParentDomain.meta, findings, entity)  # only declared types
    assert findings[0].value == "example.org"


async def test_network_shuttle() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ip": "192.0.2.1"})

    ctx = fake_context(MyNetworkShuttle.meta, transport=httpx.MockTransport(handler))
    findings = await collect(
        MyNetworkShuttle(), entity_input(EntityType.DOMAIN, "example.org"), ctx=ctx
    )
```

`fake_context()` without a transport refuses every request, so a test cannot reach the internet by accident; with a `MockTransport`, the scheme check, rate limit and size cap still apply. Use documentation-only values in tests (`example.org`, RFC 5737 addresses).

#### Testing networked Shuttles with fakes

Unit tests never use the real network. For HTTP, pass a `MockTransport` as above; the [crt.sh Shuttle's tests](shuttles/crtsh/tests/) are a fuller example, with a fake server that routes by query parameter, JSON fixtures in `tests/fixtures/`, fake time for the rate limit and backoff (give `HostRateLimiter` and the Shuttle the same fake `clock` and `sleep`), and test certificates generated with `cryptography`. For anything else, keep the protocol behind a small interface your Shuttle receives in its constructor, and give it a fake in tests; the worker creates Shuttles without arguments, so the real client is the default. The [DNS Shuttle](shuttles/dns/) does exactly that: `DnsShuttle(client=..., config=...)` takes any object with `async query(name, rdtype) -> Answer | None`, and its tests answer from a table of real dnspython records:

```python
import dns.rdata, dns.rdataclass, dns.rdatatype

from nightloom_shuttle_dns import DnsShuttle
from nightloom_shuttle_dns.client import Answer
from nightloom_shuttle_dns.config import DnsConfig

R = dns.rdatatype.RdataType


class FakeDns:
    def __init__(self, table: dict[tuple[str, R], Answer]) -> None:
        self.table = table

    async def query(self, name: str, rdtype: R) -> Answer | None:
        return self.table.get((name, rdtype))  # None: NXDOMAIN or no records


def answer(rdtype: R, *texts: str, name: str = "example.org") -> Answer:
    records = tuple(dns.rdata.from_text(dns.rdataclass.IN, rdtype, t) for t in texts)
    return Answer(name=name, records=records)


async def test_mx() -> None:
    fake = FakeDns({("example.org", R.MX): answer(R.MX, "10 mail.example.org.")})
    entity = entity_input(EntityType.DOMAIN, "example.org")
    findings = await collect(DnsShuttle(client=fake, config=DnsConfig()), entity)
```

Test the real client separately by replacing what it wraps (the DNS tests give `ResolverClient` a factory of fake resolvers that time out, fail or answer), so retries, timeouts and the per-resolver rate limit are covered without packets. A test that really needs the internet is marked `@pytest.mark.network`: it is excluded by default and in CI, and runs with `make test-network`. For the whole stack, point the Shuttle at a local server instead, as `make up TEST_DNS=1` does for DNS.

### Adding a built-in Shuttle

Put it in `shuttles/<name>/` (package `nightloom-shuttle-<name>`, Apache-2.0, with a `README.md`, `py.typed` and tests in `shuttles/<name>/tests/`). The uv workspace picks up every directory under `shuttles/`. Add it as a dependency of `apps/worker/pyproject.toml`, add its `pyproject.toml` to the bind mounts in `apps/api/Dockerfile`, and add its `src` and `tests` to `[tool.mypy]` in the root `pyproject.toml` and its import name to `--cov` and to Ruff's `known-first-party` there, and add its name to the list the CI `docker` job checks in the image. List it under [Built-in Shuttles](#built-in-shuttles) and in the README. `make check` then lints, type-checks and tests it with the rest.
