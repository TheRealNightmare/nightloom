.DEFAULT_GOAL := help

# Local stack (deploy/compose.yaml). Targets other than `up` fall back to
# .env.example when .env does not exist yet, so they still work.
ENV_FILE := $(if $(wildcard .env),.env,.env.example)
COMPOSE := docker compose -f deploy/compose.yaml --env-file $(ENV_FILE)
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
# they never touch development data. Only these two variables are exported (the
# env file is read in a subshell); values already set in the environment win,
# which is how CI passes its own service URLs.
TEST_ENV = export $$(. ./$(ENV_FILE) && \
	echo "NIGHTLOOM_TEST_DATABASE_URL=$${NIGHTLOOM_TEST_DATABASE_URL:-postgresql+asyncpg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@127.0.0.1:$${POSTGRES_HOST_PORT:-5432}/nightloom_test}" \
	     "NIGHTLOOM_TEST_VALKEY_URL=$${NIGHTLOOM_TEST_VALKEY_URL:-redis://:$${VALKEY_PASSWORD}@127.0.0.1:$${VALKEY_HOST_PORT:-6379}/15}") &&

ALEMBIC := uv run alembic -c apps/api/alembic.ini

# Web frontend (pnpm workspace at the repository root; app in apps/web).
WEB := pnpm --dir apps/web

.PHONY: help install lint fmt typecheck test test-unit test-integration check check-all api \
	up deps-up down logs ps reset compose-config migrate revision create-admin \
	web-install web-dev web-lint web-fmt web-typecheck web-test web-build check-web e2e

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

test: ## Run unit and database tests with coverage (needs `make deps-up` or `make up`)
	@$(TEST_ENV) uv run pytest

test-unit: ## Run only the tests that need no services (no coverage gate)
	uv run pytest -m "not db and not integration" --no-cov

test-integration: ## Run integration tests against the running stack (needs `make up`)
	@$(STACK_ENV) uv run pytest -m integration --no-cov

check: lint typecheck test ## Run lint, typecheck and tests (Python)

check-all: check check-web ## Run every check: Python and web

api: ## Run the API dev server on http://127.0.0.1:8000
	uv run python -m nightloom_api

up: ## Start the local stack (creates .env from .env.example if missing)
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example. Change the change-me-... passwords in it."; \
	fi
	docker compose -f deploy/compose.yaml --env-file .env up -d --build --wait

deps-up: ## Start only PostgreSQL and Valkey (enough for `make test`)
	$(COMPOSE) up -d --wait postgres valkey

down: ## Stop the local stack (keeps data volumes)
	$(COMPOSE) down

logs: ## Follow stack logs (one service: make logs SERVICE=api)
	$(COMPOSE) logs -f $(SERVICE)

ps: ## Show stack containers and their health (including the exited migrate job)
	$(COMPOSE) ps -a

reset: ## Stop the stack and DELETE all its data volumes (asks first)
	@printf 'This permanently deletes ALL local Nightloom data (PostgreSQL, Neo4j, Valkey, S3).\nType "yes" to continue: '; \
	read answer; \
	if [ "$$answer" != "yes" ]; then echo "Aborted."; exit 1; fi
	$(COMPOSE) down -v --remove-orphans

migrate: ## Apply database migrations (runs the one-shot migrate service)
	$(COMPOSE) build api
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

compose-config: ## Validate deploy/compose.yaml
	$(COMPOSE) config --quiet
	@echo "deploy/compose.yaml is valid (env file: $(ENV_FILE))."
