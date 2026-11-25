# syntax=docker/dockerfile:1
#
# Nightloom backend image: the API and the worker (same image, different
# command), with the Shuttle SDK and the built-in Shuttles. Build from the
# repository root (the uv workspace and uv.lock live there):
#
#   docker build -f apps/api/Dockerfile -t nightloom-api .
#
# A new built-in Shuttle under shuttles/ needs a bind mount for its
# pyproject.toml below and a dependency in apps/worker/pyproject.toml.

ARG PYTHON_IMAGE=python:3.12.16-slim

# --- builder: resolve the locked dependencies into /app/.venv ----------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so source changes do not invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps/api/pyproject.toml,target=apps/api/pyproject.toml \
    --mount=type=bind,source=apps/worker/pyproject.toml,target=apps/worker/pyproject.toml \
    --mount=type=bind,source=packages/core/pyproject.toml,target=packages/core/pyproject.toml \
    --mount=type=bind,source=packages/sdk/pyproject.toml,target=packages/sdk/pyproject.toml \
    --mount=type=bind,source=shuttles/active/pyproject.toml,target=shuttles/active/pyproject.toml \
    --mount=type=bind,source=shuttles/crtsh/pyproject.toml,target=shuttles/crtsh/pyproject.toml \
    --mount=type=bind,source=shuttles/dns/pyproject.toml,target=shuttles/dns/pyproject.toml \
    --mount=type=bind,source=shuttles/gleif/pyproject.toml,target=shuttles/gleif/pyproject.toml \
    --mount=type=bind,source=shuttles/hibp/pyproject.toml,target=shuttles/hibp/pyproject.toml \
    --mount=type=bind,source=shuttles/internetdb/pyproject.toml,target=shuttles/internetdb/pyproject.toml \
    --mount=type=bind,source=shuttles/rdap/pyproject.toml,target=shuttles/rdap/pyproject.toml \
    --mount=type=bind,source=shuttles/ripestat/pyproject.toml,target=shuttles/ripestat/pyproject.toml \
    --mount=type=bind,source=shuttles/registrable-domain/pyproject.toml,target=shuttles/registrable-domain/pyproject.toml \
    --mount=type=bind,source=shuttles/screenshot/pyproject.toml,target=shuttles/screenshot/pyproject.toml \
    --mount=type=bind,source=shuttles/wayback/pyproject.toml,target=shuttles/wayback/pyproject.toml \
    --mount=type=bind,source=shuttles/web/pyproject.toml,target=shuttles/web/pyproject.toml \
    --mount=type=bind,source=shuttles/wikidata/pyproject.toml,target=shuttles/wikidata/pyproject.toml \
    uv sync --locked --no-dev --no-install-workspace --package nightloom-worker

# Then the workspace packages, installed non-editable so the venv is
# self-contained: nightloom-worker pulls in the API, the shared entity model
# (nightloom-core), the Shuttle SDK and the built-in Shuttles.
COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY packages/sdk ./packages/sdk
COPY shuttles ./shuttles
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package nightloom-worker

# --- runtime: just Python and the venv ---------------------------------------
FROM ${PYTHON_IMAGE}

# libatomic1: the Node.js driver bundled with the Python `playwright` package
# (the screenshot Shuttle's client for the browser service) needs it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 nightloom \
    && useradd --system --uid 10001 --gid nightloom --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin nightloom

# Owned by root and read-only for the app user.
COPY --from=builder /app/.venv /app/.venv
# Database migrations, run by the Compose `migrate` service: `alembic upgrade head`.
COPY apps/api/alembic.ini /app/alembic.ini
COPY apps/api/alembic /app/alembic

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER 10001:10001

EXPOSE 8000

# No curl in the slim image; use the Python standard library instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

# The worker runs the same image with
#   celery -A nightloom_worker.celery_app worker --queues shuttles
# (see deploy/compose.yaml); its health check is `celery inspect ping`.
#
# 0.0.0.0 is the container's own interface; Compose publishes it on 127.0.0.1.
# --proxy-headers (uvicorn's default, spelled out) honours X-Forwarded-For and
# X-Forwarded-Proto only from the addresses in FORWARDED_ALLOW_IPS (default
# 127.0.0.1); Compose sets it to the `web` proxy's fixed address.
CMD ["uvicorn", "nightloom_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
