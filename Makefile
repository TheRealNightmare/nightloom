.DEFAULT_GOAL := help

# Local stack (deploy/compose.yaml). Targets other than `up` fall back to
# .env.example when .env does not exist yet, so they still work.
ENV_FILE := $(if $(wildcard .env),.env,.env.example)
# TEST_DNS=1 adds deploy/compose.test.yaml: a test-only local DNS server that
# the worker's DNS Shuttle asks instead of the public resolvers (CI uses it so
# DNS jobs never touch the internet). Pass it to up, test-integration and down.
#
# SCREENSHOTS=1 starts the optional `browser` service (Compose profile
# `screenshots`) that the screenshot Shuttle draws pages in; about 1 GB of
# image. Pass it to up, down, ps and logs; `make check-browser` checks it.
#
# Production (docs/DEPLOYMENT.md): when the env file says
# NIGHTLOOM_ENV=production (`make secrets` writes that), every target also
# uses deploy/compose.prod.yaml, and deploy/compose.small.yaml when it says
# NIGHTLOOM_SMALL_HOST=true. Override with PROD=0 / PROD=1, SMALL=0 / SMALL=1.
# EDGE_TEST=1 adds deploy/compose.edge-test.yaml (tests of the HTTPS edge
# only: Caddy on 127.0.0.1:8443 with its internal CA, PostgreSQL published).
#
# Production runs the published release images named by NIGHTLOOM_VERSION in
# the env file and never builds them. FROM_SOURCE=1 adds
# deploy/compose.source.yaml, which builds them from this checkout instead
# (CI tests the production configuration that way).
env_flag = $(shell grep -qsE '^[[:space:]]*$(1)=[[:space:]]*"?$(2)"?[[:space:]]*$$' $(ENV_FILE) && echo 1 || echo 0)
PROD ?= $(call env_flag,NIGHTLOOM_ENV,production)
SMALL ?= $(call env_flag,NIGHTLOOM_SMALL_HOST,(true|1))
COMPOSE_FILES := -f deploy/compose.yaml \
	$(if $(filter 1,$(PROD)),-f deploy/compose.prod.yaml) \
	$(if $(filter 1,$(SMALL)),-f deploy/compose.small.yaml) \
	$(if $(filter 1,$(PROD)),$(if $(FROM_SOURCE),-f deploy/compose.source.yaml)) \
	$(if $(EDGE_TEST),-f deploy/compose.edge-test.yaml) \
	$(if $(TEST_DNS),-f deploy/compose.test.yaml)
COMPOSE_PROFILES_ARGS := $(if $(SCREENSHOTS),--profile screenshots)
COMPOSE := docker compose $(COMPOSE_FILES) $(COMPOSE_PROFILES_ARGS) --env-file $(ENV_FILE)
# 1 when the stack runs the published images (production, not FROM_SOURCE).
PUBLISHED_IMAGES := $(if $(filter 1,$(PROD)),$(if $(FROM_SOURCE),0,1),0)
SERVICE ?=

# Shell prefix for host-side tools that talk to the running stack (integration
# tests, Alembic autogenerate). It loads the env file for the credentials and
# points the NIGHTLOOM_* connection variables at the ports Compose publishes on
# 127.0.0.1 instead of the in-network service names.
STACK_ENV = set -a && . ./$(ENV_FILE) && set +a && \
	NIGHTLOOM_DATABASE_URL="postgresql+asyncpg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@127.0.0.1:$${POSTGRES_HOST_PORT:-5432}/$${POSTGRES_DB}" \
	NIGHTLOOM_NEO4J_URI="bolt://127.0.0.1:$${NEO4J_BOLT_HOST_PORT:-7687}" \
	NIGHTLOOM_VALKEY_URL="redis://:$${VALKEY_PASSWORD}@127.0.0.1:$${VALKEY_HOST_PORT:-6379}/0" \
	NIGHTLOOM_S3_ENDPOINT="http://127.0.0.1:$${S3_HOST_PORT:-8333}" \
	NIGHTLOOM_TEST_API_URL="http://127.0.0.1:$${API_HOST_PORT:-8000}" \
	NIGHTLOOM_TEST_WEB_URL="http://127.0.0.1:$${WEB_HOST_PORT:-8080}"

# Shell prefix for `make test`: points the database-backed tests at a separate
# `nightloom_test` database and Valkey database 15 on the published ports, so
# they never touch development data, and at Neo4j on its published Bolt port
# (Community Edition has a single database; the tests only create and delete
# graphs of their own random case IDs). Only these variables are exported (the
# env file is read in a subshell); values already set in the environment win,
# which is how CI passes its own service URLs.
TEST_ENV = export $$(. ./$(ENV_FILE) && \
	echo "NIGHTLOOM_TEST_DATABASE_URL=$${NIGHTLOOM_TEST_DATABASE_URL:-postgresql+asyncpg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@127.0.0.1:$${POSTGRES_HOST_PORT:-5432}/nightloom_test}" \
	     "NIGHTLOOM_TEST_VALKEY_URL=$${NIGHTLOOM_TEST_VALKEY_URL:-redis://:$${VALKEY_PASSWORD}@127.0.0.1:$${VALKEY_HOST_PORT:-6379}/15}" \
	     "NIGHTLOOM_TEST_NEO4J_URI=$${NIGHTLOOM_TEST_NEO4J_URI:-bolt://127.0.0.1:$${NEO4J_BOLT_HOST_PORT:-7687}}" \
	     "NIGHTLOOM_TEST_NEO4J_PASSWORD=$${NIGHTLOOM_TEST_NEO4J_PASSWORD:-$${NEO4J_PASSWORD}}") &&

ALEMBIC := uv run alembic -c apps/api/alembic.ini

# Web frontend (pnpm workspace at the repository root; app in apps/web).
WEB := pnpm --dir apps/web

# Python versions the shared entity model (packages/core) is tested on by
# `make test-core-compat` and the CI `core-compat` job: the oldest and newest
# supported 3.12 patch releases and 3.13. Canonical keys must be identical on
# all of them (ipaddress, for one, changed its output between 3.12 patches).
CORE_COMPAT_PYTHONS ?= 3.12.0 3.12 3.13
# Only nightloom-core and its test tools, without the workspace's lock or dev group.
CORE_COMPAT_RUN = uv run --python $(1) --no-project --with ./packages/core \
	--with "pytest>=8.4" --with "hypothesis>=6.170.15" \
	pytest packages/core/tests -q -p no:cacheprovider -o addopts="" \
	-W ignore::pytest.PytestConfigWarning

.PHONY: help install lint fmt typecheck test test-unit test-core-compat test-integration test-network \
	check check-all check-versions bump-version api \
	worker shuttles \
	up deps-up down logs ps reset compose-config migrate revision create-admin \
	secrets deploy-pull backup restore test-edge test-nginx-edge test-backup-restore \
	web-install web-dev web-lint web-fmt web-typecheck web-test web-build web-size check-web e2e \
	screenshots

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install all workspace packages and dev tools
	uv sync

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Format code and apply lint fixes
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Run mypy (strict)
	uv run mypy

test: ## Run unit, database and graph tests with coverage (needs `make deps-up` or `make up`)
	@$(TEST_ENV) uv run pytest

test-unit: ## Run only the tests that need no services (no coverage gate)
	uv run pytest -m "not db and not graph and not integration" --no-cov

test-core-compat: ## Run the packages/core tests on every Python in CORE_COMPAT_PYTHONS
	@set -e; for python in $(CORE_COMPAT_PYTHONS); do \
		echo "== packages/core on Python $$python"; \
		$(call CORE_COMPAT_RUN,$$python); \
	done

test-integration: ## Run integration tests against the running stack (needs `make up`; TEST_DNS=1 adds the DNS job test)
	@$(STACK_ENV) $(if $(TEST_DNS),NIGHTLOOM_TEST_DNS=1) uv run pytest -m integration --no-cov

check-browser: ## Check the screenshot browser service in the running stack (needs `make up SCREENSHOTS=1`): isolated, and the worker draws pages in it
	deploy/tests/check-browser.sh docker compose $(COMPOSE_FILES) --profile screenshots --env-file $(ENV_FILE)

test-network: ## Run the opt-in tests that use the real internet (public DNS resolvers, crt.sh, RDAP servers, RIPEstat, InternetDB, Wayback Machine)
	uv run pytest -m network --no-cov

check: lint typecheck test ## Run lint, typecheck and tests (Python)

check-all: check-versions check check-web ## Run every check: versions, Python and web

check-versions: ## Check that every package has the same version (scripts/check-versions.py)
	@python3 scripts/check-versions.py

# docs/RELEASING.md. Edits every manifest and refreshes uv.lock and
# pnpm-lock.yaml; CHANGELOG.md is updated by hand.
bump-version: ## Set the version everywhere: make bump-version V=0.2.0 (or V=0.2.0-rc.1)
	@if [ -z "$(V)" ]; then echo 'Usage: make bump-version V=X.Y.Z (or X.Y.Z-rc.N)'; exit 1; fi
	@python3 scripts/bump-version.py "$(V)"

api: ## Run the API dev server on http://127.0.0.1:8000
	uv run python -m nightloom_api

# The local worker talks to the stack's PostgreSQL, Neo4j and Valkey through
# their published ports (STACK_ENV). Stop the stack's own worker first
# (docker compose ... stop worker) if you want this one to take every job.
worker: ## Run a Shuttle worker locally against the stack's services (needs `make deps-up`)
	@$(STACK_ENV) uv run celery --app=nightloom_worker.celery_app worker \
		--queues=shuttles --concurrency=2 --loglevel=INFO --without-gossip --without-mingle

shuttles: ## List the installed Shuttles (the registry CLI)
	uv run python -m nightloom_sdk.registry list

# `up` always uses .env (creating it first), unless ENV_FILE is given on the
# command line (make up ENV_FILE=.env.staging).
UP_ENV_FILE := $(if $(filter command line,$(origin ENV_FILE)),$(ENV_FILE),.env)

up: ## Start the stack (creates .env from .env.example if missing; production with a production .env; SCREENSHOTS=1 adds the browser service)
	@if [ ! -f $(UP_ENV_FILE) ]; then \
		cp .env.example $(UP_ENV_FILE); \
		echo "Created $(UP_ENV_FILE) from .env.example. Change the change-me-... passwords in it."; \
	fi
	docker compose $(COMPOSE_FILES) $(COMPOSE_PROFILES_ARGS) --env-file $(UP_ENV_FILE) up -d --build --wait

deps-up: ## Start only PostgreSQL, Valkey and Neo4j (enough for `make test`)
	$(COMPOSE) up -d --wait postgres valkey neo4j

down: ## Stop the local stack (keeps data volumes; also removes the TEST_DNS server)
	$(COMPOSE) down --remove-orphans

logs: ## Follow stack logs (one service: make logs SERVICE=api)
	$(COMPOSE) logs -f $(SERVICE)

ps: ## Show stack containers and their health (including the exited migrate job)
	$(COMPOSE) ps -a

reset: ## Stop the stack and DELETE all its data volumes (asks first)
	@printf 'This permanently deletes ALL local Nightloom data (PostgreSQL, Neo4j, Valkey, S3).\nType "yes" to continue: '; \
	read answer; \
	if [ "$$answer" != "yes" ]; then echo "Aborted."; exit 1; fi
	$(COMPOSE) down -v --remove-orphans

migrate: ## Apply the database migrations and the Neo4j schema (one-shot migrate service)
	$(if $(filter 1,$(PUBLISHED_IMAGES)),$(COMPOSE) pull migrate,$(COMPOSE) build api)
	$(COMPOSE) run --rm migrate

revision: ## Autogenerate a migration against the running stack: make revision m="add cases"
	@if [ -z "$(m)" ]; then echo 'Usage: make revision m="short description"'; exit 1; fi
	@$(STACK_ENV) $(ALEMBIC) revision --autogenerate -m "$(m)"

create-admin: ## Create an admin user in the stack: make create-admin email=you@example.org name="Your Name"
	@if [ -z "$(email)" ] || [ -z "$(name)" ]; then \
		echo 'Usage: make create-admin email=you@example.org name="Your Name"'; exit 1; \
	fi
	$(COMPOSE) run --rm api python -m nightloom_api.cli create-admin \
		--email "$(email)" --display-name "$(name)"

web-install: ## Install the web frontend's locked dependencies (pnpm)
	pnpm install --frozen-lockfile

web-dev: ## Run the web dev server on http://127.0.0.1:5173 (proxies /api to :8000)
	$(WEB) dev

web-lint: ## Lint the web frontend (ESLint) and check formatting (Prettier)
	$(WEB) exec eslint . --max-warnings=0
	$(WEB) exec prettier --check .

web-fmt: ## Format the web frontend and apply lint fixes
	$(WEB) exec prettier --write .
	$(WEB) exec eslint . --fix

web-typecheck: ## Type-check the web frontend (tsc, strict)
	$(WEB) typecheck

web-test: ## Run the web unit tests with coverage
	$(WEB) test:coverage

web-build: ## Build the web frontend into apps/web/dist
	$(WEB) build

web-size: ## Check the built bundle against apps/web/bundle-budget.json (after web-build)
	$(WEB) size

check-web: web-lint web-typecheck web-test ## Run lint, typecheck and tests (web)

# Browser end-to-end tests (Playwright, apps/web/e2e/) against the running
# stack. The account comes from NIGHTLOOM_E2E_EMAIL / NIGHTLOOM_E2E_PASSWORD in
# the environment: it is created as an admin on the first run and reused after
# that ("already exists" is not an error), so keep the same password. Needs
# Playwright's Chromium: pnpm -C apps/web exec playwright install chromium
E2E_DISPLAY_NAME ?= E2E Admin

e2e: ## Run the browser end-to-end tests (needs `make up` and NIGHTLOOM_E2E_EMAIL/_PASSWORD)
	@if [ -z "$$NIGHTLOOM_E2E_EMAIL" ] || [ -z "$$NIGHTLOOM_E2E_PASSWORD" ]; then \
		echo 'Set NIGHTLOOM_E2E_EMAIL and NIGHTLOOM_E2E_PASSWORD (a throwaway admin for the local stack).'; \
		exit 1; \
	fi; \
	web_url="http://127.0.0.1:$$(. ./$(ENV_FILE) && echo "$${WEB_HOST_PORT:-8080}")"; \
	if ! curl -fsS -o /dev/null "$$web_url/healthz"; then \
		echo "Nothing answers at $$web_url. Start the stack with 'make up' first."; \
		exit 1; \
	fi; \
	out=$$(printf '%s\n' "$$NIGHTLOOM_E2E_PASSWORD" | \
		$(COMPOSE) run --rm --no-deps -T api python -m nightloom_api.cli create-admin \
		--email "$$NIGHTLOOM_E2E_EMAIL" --display-name "$(E2E_DISPLAY_NAME)" --password-stdin 2>&1); \
	status=$$?; \
	if [ $$status -eq 0 ]; then \
		echo "Created the e2e admin $$NIGHTLOOM_E2E_EMAIL."; \
	elif printf '%s' "$$out" | grep -q 'already exists'; then \
		echo "The e2e admin $$NIGHTLOOM_E2E_EMAIL already exists; reusing it."; \
	else \
		printf '%s\n' "$$out" >&2; \
		exit $$status; \
	fi; \
	NIGHTLOOM_E2E_BASE_URL="$$web_url" $(WEB) e2e

# README screenshots (docs/images/). They are taken on a throwaway stack of
# their own: another Compose project with its own volumes, ports and network,
# started empty from .env.example (development settings) and removed again
# afterwards, so the stack of `make up` and its data are never touched. Its
# worker resolves names with the test DNS server (deploy/compose.test.yaml),
# so the demo jobs never reach the internet. The demo admin and its random
# password exist only in that stack. PNGs are shrunk to at most 300 KiB with
# Pillow (scripts/optimize-png.py, fetched by uv for the run).
SHOTS_COMPOSE := NIGHTLOOM_NETWORK_SUBNET=172.31.231.0/24 NIGHTLOOM_NETWORK_IP_RANGE=172.31.231.128/25 \
	NIGHTLOOM_WEB_IP=172.31.231.10 NIGHTLOOM_TEST_DNS_IP=172.31.231.53 \
	WEB_HOST_PORT=18080 API_HOST_PORT=18000 POSTGRES_HOST_PORT=15432 NEO4J_HTTP_HOST_PORT=17474 \
	NEO4J_BOLT_HOST_PORT=17687 VALKEY_HOST_PORT=16379 S3_HOST_PORT=18333 \
	docker compose -p nightloom-screenshots -f deploy/compose.yaml -f deploy/compose.test.yaml \
	--env-file .env.example
SHOTS_EMAIL := demo-admin@example.org
SHOTS_DIR := docs/images

screenshots: ## Take the README screenshots (docs/images/) on a throwaway stack of their own (needs Docker)
	@set -e; \
	trap '$(SHOTS_COMPOSE) down -v --remove-orphans >/dev/null 2>&1 || true' EXIT; \
	$(SHOTS_COMPOSE) down -v --remove-orphans >/dev/null 2>&1 || true; \
	$(SHOTS_COMPOSE) up -d --build --wait; \
	password=$$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))'); \
	printf '%s\n' "$$password" | $(SHOTS_COMPOSE) run --rm --no-deps -T api \
		python -m nightloom_api.cli create-admin --email "$(SHOTS_EMAIL)" --display-name "Demo Admin" --password-stdin; \
	NIGHTLOOM_SHOTS_URL=http://127.0.0.1:18080 NIGHTLOOM_SHOTS_EMAIL="$(SHOTS_EMAIL)" \
	NIGHTLOOM_SHOTS_PASSWORD="$$password" NIGHTLOOM_SHOTS_OUT="$(CURDIR)/$(SHOTS_DIR)" \
	NIGHTLOOM_SHOTS_COMPOSE='$(SHOTS_COMPOSE)' \
		node apps/web/scripts/screenshots.mjs; \
	uv run --no-project --with pillow==13.0.0 python scripts/optimize-png.py --max-kb 300 $(SHOTS_DIR)/*.png

compose-config: ## Validate the Compose files in use (the production ones too when .env says so)
	$(COMPOSE) config --quiet
	@echo "Compose configuration is valid ($(strip $(COMPOSE_FILES)); env file: $(ENV_FILE))."

# --- Production ------------------------------------------------------------------
# See docs/DEPLOYMENT.md. The scripts take the Compose command from
# NIGHTLOOM_COMPOSE, so they use the same files as every other target.

secrets: ## Create .env for production with random secrets (never overwrites; ARGS="--help" for options)
	@scripts/generate-env.sh $(ARGS)

deploy-pull: ## Production: download the images of NIGHTLOOM_VERSION (and the pinned third-party ones)
	@if [ "$(PUBLISHED_IMAGES)" != 1 ]; then \
		echo 'deploy-pull is for production with the release images (NIGHTLOOM_ENV=production, no FROM_SOURCE).'; \
		exit 1; \
	fi
	$(COMPOSE) pull
	@echo "Pulled the images for Nightloom $$(. ./$(ENV_FILE) && echo "$${NIGHTLOOM_VERSION}"). Start them with 'make up'."

backup: ## Back up PostgreSQL, Neo4j and the evidence bucket into backups/ (stops the API and worker briefly)
	@NIGHTLOOM_COMPOSE="$(COMPOSE)" ENV_FILE="$(ENV_FILE)" scripts/backup.sh

restore: ## Restore a backup: make restore BACKUP=backups/nightloom-<timestamp>.tar.gz (asks first)
	@if [ -z "$(BACKUP)" ]; then echo 'Usage: make restore BACKUP=backups/nightloom-<timestamp>.tar.gz'; exit 1; fi
	@NIGHTLOOM_COMPOSE="$(COMPOSE)" ENV_FILE="$(ENV_FILE)" scripts/restore.sh "$(BACKUP)"

test-nginx-edge: ## Check nginx's client-IP handling for every NIGHTLOOM_EDGE (IMAGE=nightloom-web:dev)
	deploy/tests/nginx-real-ip.sh $(or $(IMAGE),nightloom-web:dev)

# Caddy's internal root certificate (public, not a secret) is copied out of
# the container so the test verifies the TLS connection for real.
test-edge: ## Test the HTTPS edge through Caddy (needs a production .env and `make up EDGE_TEST=1`)
	@ca=$$(mktemp) && \
	$(COMPOSE) cp caddy:/data/caddy/pki/authorities/local/root.crt "$$ca" >/dev/null 2>&1 && \
	gateway=$$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' \
		"$$($(COMPOSE) ps -q caddy)") && \
	$(STACK_ENV) \
	NIGHTLOOM_TEST_EDGE_URL="https://127.0.0.1:$${EDGE_HTTPS_HOST_PORT:-8443}" \
	NIGHTLOOM_TEST_EDGE_HOST="$${NIGHTLOOM_DOMAIN}" \
	NIGHTLOOM_TEST_EDGE_CA="$$ca" \
	NIGHTLOOM_TEST_EDGE_CLIENT_IP="$$gateway" \
	uv run pytest -m integration --no-cov apps/api/tests/integration/test_edge.py

test-backup-restore: ## DESTRUCTIVE: seed, back up, delete ALL volumes, restore, verify (CI; NIGHTLOOM_TEST_DESTRUCTIVE=1)
	@NIGHTLOOM_COMPOSE="$(COMPOSE)" ENV_FILE="$(ENV_FILE)" scripts/test-backup-restore.sh
