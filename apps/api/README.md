# nightloom-api

The Nightloom HTTP API, built with FastAPI.

Run it from the repository root:

```sh
make api
```

Then open <http://127.0.0.1:8000/api/docs>. See [CONTRIBUTING.md](../../CONTRIBUTING.md#development-setup) for the full setup.

## Configuration

Settings are read from environment variables with the `NIGHTLOOM_` prefix:

| Variable | Default | Values |
| --- | --- | --- |
| `NIGHTLOOM_ENV` | `development` | `development`, `test`, `production` |
| `NIGHTLOOM_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `NIGHTLOOM_API_PREFIX` | `/api/v1` | URL prefix for versioned routes |
| `NIGHTLOOM_DATABASE_URL` | `postgresql+asyncpg://…@127.0.0.1:5432/nightloom` | SQLAlchemy URL (asyncpg driver) |
| `NIGHTLOOM_NEO4J_URI` | `bolt://127.0.0.1:7687` | Neo4j Bolt URI |
| `NIGHTLOOM_NEO4J_USER` / `NIGHTLOOM_NEO4J_PASSWORD` | `neo4j` / placeholder | Neo4j credentials |
| `NIGHTLOOM_VALKEY_URL` | `redis://:…@127.0.0.1:6379/0` | Valkey URL (Redis protocol) |
| `NIGHTLOOM_S3_ENDPOINT` | `http://127.0.0.1:8333` | S3-compatible endpoint (path-style addressing) |
| `NIGHTLOOM_S3_ACCESS_KEY` / `NIGHTLOOM_S3_SECRET_KEY` | placeholders | S3 credentials |
| `NIGHTLOOM_S3_BUCKET` / `NIGHTLOOM_S3_REGION` | `nightloom-evidence` / `us-east-1` | Evidence bucket and region |
| `NIGHTLOOM_DEPENDENCY_TIMEOUT_SECONDS` | `2.0` | Time limit for each `/ready` check |
| `NIGHTLOOM_COOKIE_SECURE` | `false` | Send auth cookies with `Secure` (always on in production) |

The connection defaults match the local stack's published ports and the placeholder credentials in [`.env.example`](../../.env.example). With `NIGHTLOOM_ENV=production` the API refuses to start while any of these secrets still contains the `change-me` placeholder. Passwords, keys and URLs that contain a password are never shown in logs or `repr()`.

## Endpoints

- `GET /api/v1/health`: liveness; always 200 while the process runs.
- `GET /api/v1/ready`: readiness; 200 when PostgreSQL, Neo4j, Valkey and object storage all answer, otherwise 503 with the failing checks.
- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `POST /api/v1/auth/password`: sign-in with server-side sessions in cookies.
- `GET|POST /api/v1/users`, `PATCH /api/v1/users/{id}`, `DELETE /api/v1/users/{id}/sessions`: user management (admins).

`/health` and `/ready` are public; everything else needs a session. See [Authentication](../../docs/ARCHITECTURE.md#authentication) for sessions, CSRF, roles and rate limits.

Create the first admin with `python -m nightloom_api.cli create-admin --email ... --display-name ...` (or `make create-admin` against the Compose stack).

Migrations live in [`alembic/`](alembic/); see [CONTRIBUTING.md](../../CONTRIBUTING.md#database-migrations).
