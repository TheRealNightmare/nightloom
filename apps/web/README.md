# Nightloom web

The Nightloom UI: React + Vite + TypeScript, with the investigation graph rendered by [Sigma.js](https://www.sigmajs.org/) on top of [graphology](https://graphology.github.io/).

Users sign in with an account created by an admin (see [Web authentication flow](../../docs/ARCHITECTURE.md#web-authentication-flow)). The graph is still a built-in, clearly labelled **demo graph** (fictional `example.org` data); there is no real data yet.

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

| Path                                                 | Contents                                                                                        |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| [`src/api/`](src/api/)                               | Typed fetch client (timeouts, CSRF header, typed errors) and the readiness polling hook         |
| [`src/auth/`](src/auth/)                             | Auth state (`AuthProvider`, `useAuth`), the sign-in gate, error messages and password policy    |
| [`src/domain/`](src/domain/)                         | Entity types, provenance fields and formatting helpers                                          |
| [`src/demo/sampleGraph.ts`](src/demo/sampleGraph.ts) | The demo fixture (documentation-only names, IPs and AS numbers; no people)                      |
| [`src/graph/`](src/graph/)                           | Graph building and layout, pure view logic (highlight, filter, search) and the sigma component  |
| [`src/components/`](src/components/)                 | Top bar, API status, legend, search box, side panel, sign-in screen, user menu, password dialog |
| [`e2e/`](e2e/)                                       | Playwright end-to-end tests (`pnpm e2e`, run by `make e2e`)                                     |
| [`nginx/`](nginx/)                                   | nginx configuration for the container image                                                     |

## Container

[`Dockerfile`](Dockerfile) builds the app with pnpm and serves it with unprivileged nginx on port 8080, which also proxies `/api/` to `NIGHTLOOM_API_UPSTREAM` (default `http://api:8000`). Build it from the repository root:

```sh
docker build -f apps/web/Dockerfile -t nightloom-web .
```
