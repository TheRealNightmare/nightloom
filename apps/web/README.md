# Nightloom web

The Nightloom UI: React + Vite + TypeScript, with the investigation graph rendered by [Sigma.js](https://www.sigmajs.org/) on top of [graphology](https://graphology.github.io/).

Users sign in with an account created by an admin (see [Web authentication flow](../../docs/ARCHITECTURE.md#web-authentication-flow)) and work in **cases** (see the [web routes](../../docs/ARCHITECTURE.md#web-ui)). A case's _Graph_ tab shows the case's real graph, where editors add entities and relationships by hand; the built-in, clearly labelled **demo graph** (fictional `example.org` data) lives at `/demo`. Both use the same graph view (see [Graph view](../../docs/ARCHITECTURE.md#graph-view)).

## Development

From the repository root (see [CONTRIBUTING.md](../../CONTRIBUTING.md#web-frontend) for the toolchain):

```sh
make web-install   # pnpm install --frozen-lockfile
make api           # or `make up` for the whole stack
make web-dev       # http://127.0.0.1:5173, proxies /api to http://127.0.0.1:8000
make check-web     # ESLint + Prettier, tsc, Vitest with coverage
make web-build     # production build into dist/
make e2e           # Playwright end-to-end tests against `make up` (see CONTRIBUTING)
```

| Variable                     | Default                 | Description                         |
| ---------------------------- | ----------------------- | ----------------------------------- |
| `NIGHTLOOM_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the dev server proxies `/api` |

## Layout

| Path                                 | Contents                                                                                                           |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| [`src/app/`](src/app/)               | Route table, app shell (top bar + page) and the 404 page                                                           |
| [`src/api/`](src/api/)               | Typed fetch client (timeouts, CSRF header, typed errors), case and graph calls, the schema, readiness polling hook |
| [`src/auth/`](src/auth/)             | Auth state (`AuthProvider`, `useAuth`), the sign-in gate, error messages and password policy                       |
| [`src/cases/`](src/cases/)           | Case list, create form, workspace and its tabs (the graph tab's forms in `graph/`), labels, form validation        |
| [`src/domain/`](src/domain/)         | Entity types, provenance fields and formatting helpers                                                             |
| [`src/demo/`](src/demo/)             | The demo page and its fixture (documentation-only names, IPs and AS numbers; no people)                            |
| [`src/graph/`](src/graph/)           | The shared `GraphView`, incremental graph sync, time-sliced layout, pure view logic and the sigma component        |
| [`src/components/`](src/components/) | Top bar, API status, legend, search box, side panel, sign-in screen, user menu, password dialog                    |
| [`e2e/`](e2e/)                       | Playwright end-to-end tests (`pnpm e2e`, run by `make e2e`)                                                        |
| [`nginx/`](nginx/)                   | nginx configuration for the container image                                                                        |

## Container

[`Dockerfile`](Dockerfile) builds the app with pnpm and serves it with unprivileged nginx on port 8080, which also proxies `/api/` to `NIGHTLOOM_API_UPSTREAM` (default `http://api:8000`). Build it from the repository root:

```sh
docker build -f apps/web/Dockerfile -t nightloom-web .
```

## Tests and test data

- Unit tests (Vitest, jsdom) mock sigma ([`src/test/sigmaMock.ts`](src/test/sigmaMock.ts)), since jsdom has no WebGL, and talk to a fake API ([`src/test/fakeApi.ts`](src/test/fakeApi.ts)).
- [`src/test/schema.json`](src/test/schema.json) is a copy of `GET /api/v1/schema`. An API test fails when it is out of date. To regenerate it, run `uv run python -m nightloom_api.routers.schema > apps/web/src/test/schema.json`, then `make web-fmt`.
- [`src/test/largeGraph.ts`](src/test/largeGraph.ts) generates a synthetic 2,000-entity graph for [`src/graph/performance.test.ts`](src/graph/performance.test.ts), which also adds one domain with 1,500 leaf URLs to check that filtering, grouping, expanding and collapsing stay fast. Like everything in `src/test/`, it is never part of the production bundle.
