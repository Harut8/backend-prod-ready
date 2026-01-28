# =============================================================================
# Makefile
# =============================================================================

ENV_FILE := backend/core/conf/envs/.env.local
COMPOSE_FILE := docker-compose.yml
APP_NAME := backend
APP_PORT := 8000

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

.PHONY: help env-setup install dev prod shell lint format security secrets check migrate migration clean create-admin

.DEFAULT_GOAL := help

# =============================================================================
# HELP
# =============================================================================
help:
	@echo "SETUP:"
	@echo "  env-setup   Create .env.local if missing"
	@echo "  install     Install dependencies (uv sync)"
	@echo ""
	@echo "DEV & PROD:"
	@echo "  dev         Run dev server (uvicorn with reload)"
	@echo "  prod        Run production server (docker + gunicorn)"
	@echo "  shell       Open container shell"
	@echo ""
	@echo "CODE QUALITY:"
	@echo "  lint        Lint code (ruff + mypy)"
	@echo "  format      Format code (ruff)"
	@echo "  security    Security scan (bandit + safety)"
	@echo "  secrets     Scan for leaked secrets (gitleaks)"
	@echo "  check       Run all checks (lint + security + secrets)"
	@echo ""
	@echo "DATABASE:"
	@echo "  migrate          Run migrations"
	@echo "  migration        Create migration (NAME=description)"
	@echo "  migrate-preview  Preview migration SQL without applying"
	@echo "  migrate-check    Check if migrations are up to date"
	@echo ""
	@echo "ADMIN:"
	@echo "  create-admin     Create admin user (EMAIL=x PASSWORD=y)"
	@echo ""
	@echo "CLEANUP:"
	@echo "  clean       Remove containers, volumes, dangling images, caches"

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
# DEV & PROD
# =============================================================================
dev: env-setup
	@cd backend && PYTHONPATH=.. uv run uvicorn backend.features.main:fastapi_app --reload --host 0.0.0.0 --port $(APP_PORT)

prod: env-setup
	@if [ -f docker-compose.override.yml ]; then mv docker-compose.override.yml docker-compose.override.yml.bak; fi
	@ENV_STAGE=prod docker compose -f $(COMPOSE_FILE) -f docker-compose.prod.yml --env-file $(ENV_FILE) up --build
	@if [ -f docker-compose.override.yml.bak ]; then mv docker-compose.override.yml.bak docker-compose.override.yml; fi

shell:
	@docker exec -it $(APP_NAME) /bin/bash || echo "Container not running"

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
	@docker exec $(APP_NAME) alembic upgrade head || (cd backend && POSTGRES_HOST=localhost DATABASE_URL="" uv run alembic upgrade head)

migration:
	@if [ -z "$(NAME)" ]; then echo "Usage: make migration NAME='description'"; exit 1; fi
	@cd backend && POSTGRES_HOST=localhost DATABASE_URL="" uv run alembic revision --autogenerate -m "$(NAME)"

migrate-preview:
	@echo "Preview migration SQL (dry-run):"
	@cd backend && POSTGRES_HOST=localhost DATABASE_URL="" uv run alembic upgrade head --sql

migrate-check:
	@echo "Checking if migrations are up to date..."
	@cd backend && POSTGRES_HOST=localhost DATABASE_URL="" uv run alembic check || echo "Migrations need to be updated!"

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
	@docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) down --remove-orphans 2>/dev/null || true
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
	@echo "Done"