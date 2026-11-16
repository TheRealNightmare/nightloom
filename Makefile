.DEFAULT_GOAL := help

# Local stack (deploy/compose.yaml). Targets other than `up` fall back to
# .env.example when .env does not exist yet, so they still work.
ENV_FILE := $(if $(wildcard .env),.env,.env.example)
COMPOSE := docker compose -f deploy/compose.yaml --env-file $(ENV_FILE)
SERVICE ?=

.PHONY: help install lint fmt typecheck test check api \
	up down logs ps reset compose-config

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

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

test: ## Run tests with coverage
	uv run pytest

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

ps: ## Show stack containers and their health
	$(COMPOSE) ps

reset: ## Stop the stack and DELETE all its data volumes (asks first)
	@printf 'This permanently deletes ALL local Nightloom data (PostgreSQL, Neo4j, Valkey, S3).\nType "yes" to continue: '; \
	read answer; \
	if [ "$$answer" != "yes" ]; then echo "Aborted."; exit 1; fi
	$(COMPOSE) down -v --remove-orphans

compose-config: ## Validate deploy/compose.yaml
	$(COMPOSE) config --quiet
	@echo "deploy/compose.yaml is valid (env file: $(ENV_FILE))."
