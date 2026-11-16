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
	NIGHTLOOM_TEST_API_URL="http://127.0.0.1:$${API_HOST_PORT:-8000}"

ALEMBIC := uv run alembic -c apps/api/alembic.ini

.PHONY: help install lint fmt typecheck test test-integration check api \
	up down logs ps reset compose-config migrate revision

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
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

test: ## Run unit tests with coverage
	uv run pytest

test-integration: ## Run integration tests against the running stack (needs `make up`)
	@$(STACK_ENV) uv run pytest -m integration --no-cov

check: lint typecheck test ## Run lint, typecheck and tests

api: ## Run the API dev server on http://127.0.0.1:8000
	uv run python -m nightloom_api

up: ## Start the local stack (creates .env from .env.example if missing)
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example. Change the change-me-... passwords in it."; \
	fi
	docker compose -f deploy/compose.yaml --env-file .env up -d --build --wait

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

compose-config: ## Validate deploy/compose.yaml
	$(COMPOSE) config --quiet
	@echo "deploy/compose.yaml is valid (env file: $(ENV_FILE))."
