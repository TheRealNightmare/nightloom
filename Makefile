.DEFAULT_GOAL := help

.PHONY: help install lint fmt typecheck test check api

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

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
