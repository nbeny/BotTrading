.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down build logs ps migrate topics lint format test cov install

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the shared lib + dev tooling locally (editable)
	pip install -e libs/cmi_common
	pip install ruff black mypy pytest pytest-asyncio pytest-cov pre-commit

up: ## Build and start the full stack
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

build: ## Build all images
	$(COMPOSE) build

logs: ## Tail logs
	$(COMPOSE) logs -f --tail=100

ps: ## Show running services
	$(COMPOSE) ps

migrate: ## Run Alembic migrations
	$(COMPOSE) run --rm migrate alembic upgrade head

topics: ## (Re)create Kafka topics
	$(COMPOSE) run --rm kafka-init

lint: ## Lint + type-check
	ruff check libs services
	black --check libs services
	mypy libs services

format: ## Auto-format
	ruff check --fix libs services
	black libs services

test: ## Run tests
	pytest

cov: ## Run tests with coverage
	pytest --cov --cov-report=term-missing
