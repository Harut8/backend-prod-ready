# syntax=docker/dockerfile:1.4
# =============================================================================
# prod-ready-backend - Production-optimized FastAPI Docker Image
# =============================================================================

FROM python:3.11.11-slim-bookworm AS builder

# Build arguments
ARG ENV_STAGE=prod

# Python optimization
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Install build dependencies (optimized for caching)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean

# Copy UV package manager (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

# Create virtual environment
RUN uv venv /opt/venv

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies from lockfile (production-ready, reproducible)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv pip install -r pyproject.toml

# Copy application code
COPY __init__.py /app/backend/
COPY core/ /app/backend/core/
COPY features/ /app/backend/features/
COPY shared/ /app/backend/shared/
COPY logging_config.yaml alembic.ini /app/

# =============================================================================
# RUNTIME STAGE
# =============================================================================
FROM python:3.11.11-slim-bookworm AS runtime

ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000
ARG ENV_STAGE=prod

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    ENV_STAGE=${ENV_STAGE} \
    SERVER_MODE=production \
    # Granian server configuration (can be overridden at runtime)
    GRANIAN_WORKERS=8 \
    GRANIAN_BACKLOG=2048 \
    GRANIAN_BACKPRESSURE=512

# Install minimal runtime dependencies (optimized for caching)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tini \
    && apt-get clean \
    && rm -rf /tmp/* /var/tmp/*

# Create non-root user
RUN groupadd -g ${APP_GID} ${APP_USER} \
    && useradd -u ${APP_UID} -g ${APP_GID} -m -s /bin/bash ${APP_USER}

WORKDIR /app

# Create logs directory with proper permissions
RUN mkdir -p /app/logs && chown -R ${APP_USER}:${APP_USER} /app/logs

# Copy venv from builder (isolated, clean)
COPY --from=builder --chown=${APP_USER}:${APP_USER} /opt/venv /opt/venv

# Copy application
COPY --from=builder --chown=${APP_USER}:${APP_USER} /app /app

# Create __init__.py for backend package
RUN touch /app/backend/__init__.py

USER ${APP_USER}

# Health check without curl
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/system/health').read()" || exit 1

EXPOSE 8000

# Use tini for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Production command with Granian ASGI server
# Granian is a Rust-based ASGI server: 2-3x faster than Uvicorn, 50% less memory
# Using /dev/shm for worker temp directory for better performance (shared memory)
# Configuration (configurable via environment variables):
#   - GRANIAN_WORKERS: number of worker processes (default: 8)
#   - GRANIAN_BACKLOG: TCP connection backlog (default: 2048)
#   - GRANIAN_BACKPRESSURE: max concurrent connections per worker (default: 512)
#   - workers-lifetime 43200 (12 hours): restart workers to prevent memory leaks
#   - respawn-interval 30: stagger worker restarts by 30 seconds
CMD ["sh", "-c", "cd /dev/shm && exec granian \
     --interface asgi \
     --host 0.0.0.0 \
     --port 8000 \
     --workers ${GRANIAN_WORKERS:-8} \
     --loop uvloop \
     --http auto \
     --backlog ${GRANIAN_BACKLOG:-2048} \
     --backpressure ${GRANIAN_BACKPRESSURE:-512} \
     --workers-lifetime 43200 \
     --respawn-interval 30 \
     --process-name prod-ready-backend \
     --log \
     --log-level info \
     --proxy-headers \
     backend.features.main:fastapi_app"]

# =============================================================================
# DEVELOPMENT STAGE
# =============================================================================
FROM runtime AS development

USER root

# Override environment for development
ENV ENV_STAGE=dev \
    SERVER_MODE=development \
    DEBUG=true

# Install dev tools (optimized for caching)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    git \
    vim \
    && apt-get clean

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

USER ${APP_USER}

# Development with hot reload using Granian
# Granian's --reload mode watches for file changes and auto-restarts
# --reload-paths limits watching to backend directory only (excludes logs)
CMD ["sh", "-c", "echo '=== DEVELOPMENT MODE: Granian with hot-reload ===' && granian \
     --interface asgi \
     --host 0.0.0.0 \
     --port 8000 \
     --reload \
     --reload-paths /app/backend \
     --loop uvloop \
     --log \
     --log-level debug \
     backend.features.main:fastapi_app"]

# =============================================================================
# METADATA
# =============================================================================
LABEL maintainer="prod-ready-backend" \
      version="2.1.0" \
      description="Production-optimized FastAPI backend with Granian ASGI server" \
      org.opencontainers.image.title="prod-ready-backend" \
      org.opencontainers.image.vendor="prod-ready-backend"
