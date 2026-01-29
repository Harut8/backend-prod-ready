# =============================================================================
# Makefile
# =============================================================================

ENV_FILE := backend/core/conf/envs/.env.local
COMPOSE_FILE := docker-compose.yml
SERVICE_NAME := backend
CONTAINER_NAME := kinonee-backend
APP_PORT := 8000

# Worker counts by environment
WORKERS_LOCAL := 1
WORKERS_DEV := 2
WORKERS_PROD := 8

# Common env vars for docker compose commands (required vars with defaults)
DOCKER_ENV := REDIS_PASSWORD=$${REDIS_PASSWORD:-redis} \
	POSTGRES_DB=$${POSTGRES_DB:-kinonee_db} \
	POSTGRES_USER=$${POSTGRES_USER:-kinonee_user} \
	POSTGRES_PASSWORD=$${POSTGRES_PASSWORD:-kinonee_password}

# Base docker compose command
COMPOSE_CMD := $(DOCKER_ENV) docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE)

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

.PHONY: help env-setup install dev docker-dev prod infra infra-down rebuild shell logs lint format security secrets check test migrate migration clean create-admin tracing tracing-down

.DEFAULT_GOAL := help

# =============================================================================
# HELP
# =============================================================================
help:
	@echo "SETUP:"
	@echo "  env-setup      Create .env.local if missing"
	@echo "  install        Install dependencies (uv sync)"
	@echo ""
	@echo "RUNNING:"
	@echo "  dev            Local: uvicorn + hot-reload (1 worker), infra in Docker"
	@echo "  docker-dev     Docker: full stack with 2 workers"
	@echo "  prod           Docker: production mode with 8 workers"
	@echo "  infra          Start infrastructure only (postgres, redis)"
	@echo "  infra-down     Stop infrastructure services"
	@echo ""
	@echo "DOCKER:"
	@echo "  rebuild        Rebuild and restart backend container"
	@echo "  shell          Open container shell"
	@echo "  logs           Tail backend logs (N=100 for last N lines)"
	@echo ""
	@echo "TESTING:"
	@echo "  test           Run all tests (pytest)"
	@echo ""
	@echo "CODE QUALITY:"
	@echo "  lint           Lint code (ruff + mypy)"
	@echo "  format         Format code (ruff)"
	@echo "  security       Security scan (bandit + safety)"
	@echo "  secrets        Scan for leaked secrets (gitleaks)"
	@echo "  check          Run all checks (lint + security + secrets)"
	@echo ""
	@echo "DATABASE:"
	@echo "  migrate        Run migrations"
	@echo "  migration      Create migration (NAME=description)"
	@echo "  migrate-preview Preview migration SQL without applying"
	@echo "  migrate-check  Check if migrations are up to date"
	@echo ""
	@echo "ADMIN:"
	@echo "  create-admin   Create admin user (EMAIL=x PASSWORD=y)"
	@echo ""
	@echo "OBSERVABILITY:"
	@echo "  tracing        Start with Jaeger (ENV=local|dev|prod, default=dev)"
	@echo "  tracing-down   Stop tracing stack"
	@echo ""
	@echo "CLEANUP:"
	@echo "  clean          Remove containers, volumes, dangling images, caches"

# =============================================================================
# SETUP
# =============================================================================
env-setup:
	@if [ ! -f "$(ENV_FILE)" ]; then \
		cp .env.example $(ENV_FILE); \
		echo "Created .env.local - please fill in the values"; \
	fi

install:
	@cd backend && uv sync

# =============================================================================
# RUNNING
# =============================================================================

# Local development: infra in Docker, backend locally with uvicorn hot-reload
dev: env-setup infra
	@echo ""
	@echo "Starting local development server (1 worker, hot-reload)..."
	@cd backend && PYTHONPATH=.. \
		REDIS_HOST=localhost \
		POSTGRES_HOST=localhost \
		uv run uvicorn backend.features.main:fastapi_app \
		--reload \
		--host 0.0.0.0 \
		--port $(APP_PORT)

# Docker development: full stack in Docker with 2 workers (uses docker-compose.override.yml)
docker-dev: env-setup
	@echo "Starting Docker development (2 workers)..."
	@$(COMPOSE_CMD) up -d --build
	@echo ""
	@echo "Backend: http://localhost:$(APP_PORT)"

# Production: full stack with production settings
prod: env-setup
	@echo "Starting production mode (8 workers)..."
	@if [ -f docker-compose.override.yml ]; then mv docker-compose.override.yml docker-compose.override.yml.bak; fi
	@$(DOCKER_ENV) ENV_STAGE=prod docker compose -f $(COMPOSE_FILE) -f docker-compose.prod.yml --env-file $(ENV_FILE) up --build
	@if [ -f docker-compose.override.yml.bak ]; then mv docker-compose.override.yml.bak docker-compose.override.yml; fi

# Infrastructure only
infra:
	@echo "Starting infrastructure services (postgres, redis)..."
	@$(COMPOSE_CMD) up -d postgres redis
	@echo ""
	@echo "PostgreSQL: localhost:5432"
	@echo "Redis:      localhost:6379"

infra-down:
	@echo "Stopping infrastructure services..."
	@$(COMPOSE_CMD) stop postgres redis

# =============================================================================
# DOCKER MANAGEMENT
# =============================================================================
rebuild:
	@echo "Rebuilding $(SERVICE_NAME) container..."
	@$(COMPOSE_CMD) up -d --build --no-deps $(SERVICE_NAME)
	@echo "$(SERVICE_NAME) rebuilt and restarted"

shell:
	@docker exec -it $(CONTAINER_NAME) /bin/bash || echo "Container not running"

logs:
	@docker logs -f --tail $(N) $(CONTAINER_NAME) 2>/dev/null || docker logs -f --tail 100 $(CONTAINER_NAME)

# =============================================================================
# TESTING
# =============================================================================
test:
	@cd backend && PYTHONPATH=.. uv run pytest

# =============================================================================
# CODE QUALITY
# =============================================================================
lint:
	@cd backend && uv run ruff check core/ features/ --fix --unsafe-fixes
	@cd backend && uv run mypy core/ features/

format:
	@cd backend && uv run ruff format core/ features/
	@cd backend && uv run ruff check --fix --unsafe-fixes core/ features/

security:
	@cd backend && uv run bandit -r core/ features/ -ll
	@cd backend && uv run safety scan

secrets:
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --source . --verbose; \
	else \
		echo "gitleaks not installed. Install: brew install gitleaks"; \
		echo "Falling back to basic .env scan..."; \
		find . -name "*.env*" -not -path "./.git/*" -not -path "*/.venv/*" -not -path "*/node_modules/*" \
			-not -name "*.example" -not -name "*.local" | \
			xargs -I {} sh -c 'if grep -qE "^[A-Z_]+=[^\$$].+" "{}"; then echo "WARNING: {} may contain secrets"; fi'; \
	fi

check: lint security secrets

# =============================================================================
# DATABASE
# =============================================================================
migrate:
	@docker exec $(CONTAINER_NAME) alembic upgrade head

migration:
	@if [ -z "$(NAME)" ]; then echo "Usage: make migration NAME='description'"; exit 1; fi
	@docker exec $(CONTAINER_NAME) alembic revision --autogenerate -m "$(NAME)"

migrate-preview:
	@echo "Preview migration SQL (dry-run):"
	@docker exec $(CONTAINER_NAME) alembic upgrade head --sql

migrate-check:
	@echo "Checking if migrations are up to date..."
	@docker exec $(CONTAINER_NAME) alembic check || echo "Migrations need to be updated!"

# =============================================================================
# ADMIN
# =============================================================================
create-admin:
	@if [ -z "$(EMAIL)" ] || [ -z "$(PASSWORD)" ]; then \
		echo "Usage: make create-admin EMAIL=admin@example.com PASSWORD=SecurePass123!"; \
		exit 1; \
	fi
	@cd backend && PYTHONPATH=.. uv run python scripts/create_admin.py --email "$(EMAIL)" --password "$(PASSWORD)"

# =============================================================================
# CLEANUP
# =============================================================================
clean:
	@echo "Stopping containers..."
	@$(COMPOSE_CMD) down --remove-orphans 2>/dev/null || true
	@echo "Removing dangling images..."
	@docker images -qf "dangling=true" | xargs -r docker rmi -f 2>/dev/null || true
	@echo "Removing exited containers..."
	@docker ps -aqf "status=exited" | xargs -r docker rm -f 2>/dev/null || true
	@echo "Cleaning Python caches..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Cleaning uv cache..."
	@uv cache clean 2>/dev/null || true
	@echo "Done"

# =============================================================================
# OBSERVABILITY
# =============================================================================
# Usage: make tracing ENV=local|dev|prod (default: dev)
ENV ?= dev

tracing: env-setup
ifeq ($(ENV),local)
	@echo "Starting tracing (local mode: infra + Jaeger in Docker, backend locally)..."
	@$(COMPOSE_CMD) -f docker-compose.observability.yml up -d postgres redis jaeger
	@echo ""
	@echo "Jaeger UI: http://localhost:16686"
	@echo "Now run: make dev"
else ifeq ($(ENV),prod)
	@echo "Starting tracing (prod mode: 8 workers)..."
	@if [ -f docker-compose.override.yml ]; then mv docker-compose.override.yml docker-compose.override.yml.bak; fi
	@$(DOCKER_ENV) ENV_STAGE=prod docker compose -f $(COMPOSE_FILE) -f docker-compose.prod.yml -f docker-compose.observability.yml --env-file $(ENV_FILE) up -d
	@if [ -f docker-compose.override.yml.bak ]; then mv docker-compose.override.yml.bak docker-compose.override.yml; fi
	@echo ""
	@echo "Jaeger UI: http://localhost:16686"
	@echo "Backend:   http://localhost:$(APP_PORT)"
else
	@echo "Starting tracing (dev mode: 2 workers)..."
	@$(COMPOSE_CMD) -f docker-compose.observability.yml up -d --build
	@echo ""
	@echo "Jaeger UI: http://localhost:16686"
	@echo "Backend:   http://localhost:$(APP_PORT)"
endif

tracing-down:
	@echo "Stopping tracing stack..."
	@$(COMPOSE_CMD) -f docker-compose.observability.yml down
