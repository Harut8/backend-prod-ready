"""
FastAPI Application Entry Point.

This module configures and creates the FastAPI application with:
- Dependency injection containers
- Middleware stack (CORS, rate limiting, request ID, etc.)
- Exception handlers
- Observability (metrics, tracing, audit logging)
- Graceful shutdown handling
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
import logging
import signal
import sys

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic_core import _pydantic_core
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.cors import CORSMiddleware
import structlog
import tenacity

from backend.core.api.middleware.admission_control import AdmissionControlMiddleware
from backend.core.api.middleware.request_id import RequestIdMiddleware
from backend.core.conf.settings import SETTINGS
from backend.core.domain.exceptions import DomainError
from backend.core.exceptions.handlers import (
    domain_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from backend.core.infrastructure.container import InfrastructureContainer, shutdown_infrastructure
from backend.core.observability.logging import configure_logging
from backend.core.observability.metrics import setup_metrics
from backend.core.observability.tracing import setup_tracing
from backend.core.security.rate_limiting import limiter, rate_limit_exceeded_handler
from backend.features.system import SystemContainer, router as system_router_module, system_router


# =============================================================================
# Logging Configuration
# =============================================================================

configure_logging()
logger = structlog.get_logger(__name__)


# =============================================================================
# Application Class
# =============================================================================


class Application(FastAPI):
    """
    Custom FastAPI application with typed container attributes.

    Provides type-safe access to dependency injection containers.
    """

    infrastructure_container: InfrastructureContainer
    system_container: SystemContainer


# =============================================================================
# Database Initialization
# =============================================================================


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=4, max=15) + tenacity.wait_random(0, 2),
    reraise=True,
    before_sleep=tenacity.before_sleep_log(logger, logging.INFO),
)
async def _initialize_database(app: Application) -> None:
    """
    Initialize database connection with retry logic.

    Sets conservative timeouts to prevent long-running queries
    from blocking the connection pool during startup.
    """
    async with app.infrastructure_container.pg_db.provided.engine().begin() as conn:
        await conn.execute(text("SET lock_timeout = '4s'"))
        await conn.execute(text("SET statement_timeout = '8s'"))
    logger.info("Database connection initialized")


# =============================================================================
# Lifespan Management
# =============================================================================


@asynccontextmanager
async def lifespan(app: Application) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.

    Handles:
    - Startup: Database initialization, signal handler registration
    - Shutdown: Graceful cleanup of all infrastructure resources

    The ASGI lifespan protocol ensures this runs on worker startup/shutdown,
    including when receiving SIGTERM/SIGINT from orchestrators like Kubernetes.
    """
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_signal(sig: signal.Signals) -> None:
        """Handle termination signals."""
        logger.info("Received shutdown signal", signal=sig.name)
        shutdown_event.set()

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))  # type: ignore[misc]
        except (RuntimeError, NotImplementedError):
            # Signal handlers not available on all platforms (e.g., Windows)
            logger.debug("Could not register signal handler", signal=sig.name)

    try:
        # Startup
        await _initialize_database(app)
        logger.info(
            "Application started",
            app_name=SETTINGS.APP.APP_NAME,
            environment=SETTINGS.APP.ENVIRONMENT,
        )
        yield

    finally:
        # Shutdown
        logger.info("Starting graceful shutdown")
        shutdown_event.set()

        try:
            await asyncio.shield(
                shutdown_infrastructure(
                    app.infrastructure_container,
                    shutdown_timeout=SETTINGS.TIMEOUTS.SHUTDOWN_TIMEOUT,
                )
            )
        except asyncio.CancelledError:
            logger.warning("Shutdown interrupted - resources may not be fully cleaned")
        except Exception:
            logger.exception("Error during shutdown")
        finally:
            logger.info("Shutdown completed")

        # Remove signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            with suppress(RuntimeError, NotImplementedError, ValueError):
                loop.remove_signal_handler(sig)


# =============================================================================
# Container Initialization
# =============================================================================


def _init_infrastructure_container(app: Application) -> None:
    """Initialize core infrastructure container (database, cache)."""
    app.infrastructure_container = InfrastructureContainer()
    app.infrastructure_container.config.from_dict(SETTINGS.model_dump())
    app.infrastructure_container.init_resources()

    # Wire infrastructure to modules that need direct access
    app.infrastructure_container.wire(
        modules=[sys.modules[__name__]],
    )

    logger.debug("Infrastructure container initialized")


def _init_system_container(app: Application) -> None:
    """Initialize system feature container (health checks)."""
    app.system_container = SystemContainer(
        session_factory=app.infrastructure_container.session_factory,
        cache_service=app.infrastructure_container.cache_service,
    )

    app.system_container.wire(
        modules=[system_router_module],
    )

    logger.debug("System container initialized")


def _init_containers(app: Application) -> None:
    """Initialize all dependency injection containers."""
    _init_infrastructure_container(app)
    _init_system_container(app)


# =============================================================================
# Middleware Configuration
# =============================================================================


def _configure_middleware(app: Application) -> None:
    """
    Configure middleware stack.

    Order matters! Middleware is processed outside-in on request,
    inside-out on response. CORS must be outermost for preflight requests.
    """
    # CORS - must be first (outermost) for preflight handling
    app.add_middleware(
        CORSMiddleware,
        allow_origins=SETTINGS.APP.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID middleware for tracing and correlation
    app.add_middleware(RequestIdMiddleware)

    # Admission control - prevents connection pool exhaustion under burst traffic
    # Must be after request ID so rejected requests still have correlation IDs
    app.add_middleware(AdmissionControlMiddleware)

    # Rate limiter state
    app.state.limiter = limiter

    logger.debug("Middleware configured")


# =============================================================================
# Exception Handlers
# =============================================================================


def _configure_exception_handlers(app: Application) -> None:
    """Configure global exception handlers."""

    # Validation errors (Pydantic)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(_pydantic_core.ValidationError, validation_exception_handler)

    # Rate limiting
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # Domain exceptions (business rule violations)
    app.add_exception_handler(DomainError, domain_exception_handler)

    # Catch-all for unhandled exceptions (must be last)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    logger.debug("Exception handlers configured")


# =============================================================================
# Router Registration
# =============================================================================


def _register_routers(app: Application) -> None:
    """Register all API routers."""
    api_prefix = SETTINGS.API.API_V1_PREFIX

    # System routes (health, ready, status)
    app.include_router(
        system_router,
        prefix=api_prefix,
        tags=["System"],
    )

    logger.debug("Routers registered")


# =============================================================================
# Observability Setup
# =============================================================================


def _configure_observability(app: Application) -> None:
    """Configure observability features (metrics, tracing)."""
    # Prometheus metrics
    setup_metrics(app)

    # OpenTelemetry distributed tracing
    setup_tracing(app)

    logger.debug("Observability configured")


# =============================================================================
# Application Factory
# =============================================================================


def create_app() -> Application:
    """
    Create and configure the FastAPI application.

    Returns:
        Fully configured Application instance
    """
    # Determine if docs should be exposed
    is_production = SETTINGS.APP.ENVIRONMENT == "prod"
    docs_url = None if is_production else f"{SETTINGS.API.API_V1_PREFIX}/docs"
    redoc_url = None if is_production else f"{SETTINGS.API.API_V1_PREFIX}/redoc"
    openapi_url = None if is_production else f"{SETTINGS.API.API_V1_PREFIX}/openapi.json"

    app = Application(
        title=SETTINGS.APP.APP_NAME,
        description="Connect athletes with coaches - marketplace API",
        version=SETTINGS.APP.APP_VERSION,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )

    # Initialize components in order
    _init_containers(app)
    _configure_middleware(app)
    _configure_exception_handlers(app)
    _register_routers(app)
    _configure_observability(app)

    logger.info(
        "Application created",
        environment=SETTINGS.APP.ENVIRONMENT,
        docs_enabled=not is_production,
    )

    return app


# =============================================================================
# Application Instance
# =============================================================================

# Create the application instance for ASGI servers (uvicorn, gunicorn)
fastapi_app = create_app()
