# syntax=docker/dockerfile:1
#
# Nightloom web image: the built SPA served by unprivileged nginx, which also
# proxies /api/ to the API. Build from the repository root (the pnpm
# workspace and pnpm-lock.yaml live there):
#
#   docker build -f apps/web/Dockerfile -t nightloom-web .

ARG NODE_IMAGE=node:26.13.1-alpine3.24
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30.6-alpine3.24

# --- build: install the locked dependencies and build the static site -------
FROM ${NODE_IMAGE} AS build

# Node 25+ no longer bundles Corepack; install the pinned version, which then
# provides the pnpm version pinned in package.json ("packageManager").
RUN npm install --global corepack@0.36.0 && corepack enable

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    PNPM_HOME=/pnpm \
    CI=true

WORKDIR /app

# Manifests first, so source changes do not invalidate the dependency layer.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store && \
    pnpm install --frozen-lockfile --filter @nightloom/web

COPY apps/web apps/web
RUN pnpm -C apps/web build

# --- runtime: static files + nginx, running as a non-root user (uid 101) -----
FROM ${NGINX_IMAGE}

# The entrypoint renders templates into /etc/nginx/conf.d at start-up and
# fills NGINX_LOCAL_RESOLVERS from /etc/resolv.conf.
ENV NIGHTLOOM_API_UPSTREAM=http://api:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

COPY apps/web/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY apps/web/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html

USER 101
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
