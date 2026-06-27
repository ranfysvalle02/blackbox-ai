# Override if `uv` is not on PATH, e.g. `make UV=/path/to/uv test`.
UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help install dev run up down logs lint fmt typecheck test test-int check demo \
	gen-key init search

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync all dependencies (incl. dev + examples)
	$(UV) sync --extra examples

dev: ## Run the gateway locally with autoreload
	$(UV) run uvicorn blackbox_ai.main:app --reload --host 0.0.0.0 --port 8000

run: ## Run the gateway (no reload)
	$(UV) run blackbox-ai

up: ## Start gateway + MongoDB Atlas Local via docker compose
	docker compose up --build -d

down: ## Stop the docker compose stack
	docker compose down

logs: ## Tail gateway logs
	docker compose logs -f gateway

gen-key: ## Generate a local encryption key and append it to .env
	@touch .env
	@if grep -q '^GATEWAY_ENCRYPTION_KEY=.\+' .env; then \
		echo "GATEWAY_ENCRYPTION_KEY already set in .env; leaving it untouched."; \
	else \
		key=$$($(UV) run blackbox-ai gen-key); \
		grep -v '^GATEWAY_ENCRYPTION_KEY=' .env > .env.tmp 2>/dev/null || true; \
		mv .env.tmp .env 2>/dev/null || true; \
		echo "GATEWAY_ENCRYPTION_KEY=$$key" >> .env; \
		echo "Wrote GATEWAY_ENCRYPTION_KEY to .env."; \
		echo "Queryable Encryption is on by default - now run: make up && make init"; \
	fi

init: ## Bootstrap encrypted collections, indexes, TTL, and vector index (in container)
	docker compose exec gateway blackbox-ai init

search: ## Vector time-travel search in the container. Usage: make search q="why ...?"
	docker compose exec gateway blackbox-ai search "$(q)"

lint: ## Lint with ruff
	$(UV) run ruff check src tests

fmt: ## Auto-format and fix with ruff
	$(UV) run ruff format src tests examples
	$(UV) run ruff check --fix src tests examples

typecheck: ## Type-check with mypy (strict)
	$(UV) run mypy src

test: ## Run unit tests (no live MongoDB needed)
	$(UV) run pytest -m "not integration"

test-int: ## Run integration tests (requires MongoDB; set MONGO_TEST_URI)
	$(UV) run pytest -m integration

check: lint typecheck test ## Lint + typecheck + unit tests

demo: ## Drive all configured providers through the gateway via native SDKs
	$(UV) run python examples/demo.py
