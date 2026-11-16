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
