# syntax=docker/dockerfile:1
#
# Nightloom API image. Build from the repository root (the uv workspace and
# uv.lock live there):
#
#   docker build -f apps/api/Dockerfile -t nightloom-api .

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
    uv sync --locked --no-dev --no-install-workspace --package nightloom-api

# Then the API itself, installed non-editable so the venv is self-contained.
COPY pyproject.toml uv.lock ./
COPY apps/api ./apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package nightloom-api

# --- runtime: just Python and the venv ---------------------------------------
FROM ${PYTHON_IMAGE}

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

# 0.0.0.0 is the container's own interface; Compose publishes it on 127.0.0.1.
# --proxy-headers (uvicorn's default, spelled out) honours X-Forwarded-For and
# X-Forwarded-Proto only from the addresses in FORWARDED_ALLOW_IPS (default
# 127.0.0.1); Compose sets it to the `web` proxy's fixed address.
CMD ["uvicorn", "nightloom_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
