"""
FastAPI Application Entry Point.

This module configures and creates the FastAPI application with:
- Dependency injection containers
- Middleware stack (CORS, rate limiting, request ID, etc.)
- Exception handlers
- Observability (metrics, tracing, audit logging)
- Graceful shutdown handling
"""

# =============================================================================
# Logging Configuration (MUST be first, before other app imports)
# =============================================================================
# Configure logging before importing other modules to ensure all structlog
# loggers are created with the correct configuration (timestamps, colors, etc.)

from backend.core.observability.logging import configure_logging


configure_logging()

# =============================================================================
# Standard Library Imports
# =============================================================================

import asyncio  # noqa: E402
from collections.abc import AsyncGenerator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

# =============================================================================
# Third-Party Imports
# =============================================================================
from fastapi import FastAPI  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from identity_plan_kit import IdentityPlanKit  # noqa: E402
from pydantic_core import _pydantic_core  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from sqlalchemy import text  # noqa: E402
from starlette.exceptions import HTTPException  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
import structlog  # noqa: E402
import tenacity  # noqa: E402

# =============================================================================
# Application Imports
# =============================================================================
from backend.core.api.middleware.admission_control import AdmissionControlMiddleware  # noqa: E402
from backend.core.api.middleware.request_id import RequestIdMiddleware  # noqa: E402
from backend.core.conf.settings import SETTINGS  # noqa: E402
from backend.core.domain.exceptions import DomainError  # noqa: E402
from backend.core.exceptions.handlers import (  # noqa: E402
    IPK_EXCEPTION_HANDLERS,
    auth_error_handler,
    domain_exception_handler,
    feature_not_available_handler,
    http_exception_handler,
    ipk_base_error_handler,
    ipk_user_not_found_handler,
    permission_denied_handler,
    plan_expired_handler,
    plan_not_found_handler,
    quota_exceeded_handler,
    role_not_found_handler,
    token_expired_handler,
    token_invalid_handler,
    unhandled_exception_handler,
    user_inactive_handler,
    user_plan_not_found_handler,
    validation_exception_handler,
)
from backend.core.infrastructure.admin import setup_admin_panel  # noqa: E402
from backend.core.infrastructure.container import InfrastructureContainer, shutdown_infrastructure  # noqa: E402
from backend.core.infrastructure.database.error_handler import is_auth_error, is_dns_resolution_error  # noqa: E402
from backend.core.infrastructure.identity_kit import create_identity_kit  # noqa: E402
from backend.core.observability.metrics import setup_metrics  # noqa: E402
from backend.core.observability.tracing import setup_tracing  # noqa: E402
from backend.core.security.rate_limiting import limiter, rate_limit_exceeded_handler  # noqa: E402
from backend.features.system import SystemContainer, router as system_router_module, system_router  # noqa: E402


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
    identity_kit: IdentityPlanKit


# =============================================================================
# Database Initialization
# =============================================================================


def _should_retry_db_error(retry_state: tenacity.RetryCallState) -> bool:
    """Determine if database error should be retried.

    Non-retryable errors:
    - DNS resolution errors: hostname is misconfigured
    - Authentication errors: credentials are wrong, won't change on retry
    - No exception (success): don't retry successful operations
    """
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    if not exception:
        # No exception = success, don't retry
        return False
    if is_dns_resolution_error(exception):
        return False
    return not is_auth_error(exception)


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=4, max=15) + tenacity.wait_random(0, 2),
    reraise=True,
    retry=_should_retry_db_error,
    before_sleep=tenacity.before_sleep_log(logger, logging.INFO),
)
async def _initialize_database(app: Application) -> None:
    """
    Initialize database connection with retry logic.

    Sets conservative timeouts to prevent long-running queries
    from blocking the connection pool during startup.
    """

    try:
        async with app.infrastructure_container.pg_db.provided.engine().begin() as conn:
            await conn.execute(text("SET lock_timeout = '4s'"))
            await conn.execute(text("SET statement_timeout = '8s'"))
        logger.info("Database connection initialized")
    except Exception as e:
        db_url = SETTINGS.DATABASE.DATABASE_URL
        parsed = urlparse(str(db_url))

        if is_dns_resolution_error(e):
            logger.exception(
                "Database hostname cannot be resolved",
                hostname=parsed.hostname or "unknown",
                hint="Check POSTGRES_HOST in your environment file. "
                "Use 'localhost' for local development or 'postgres' inside Docker.",
            )
        elif is_auth_error(e):
            logger.exception(
                "Database authentication failed",
                username=parsed.username or "unknown",
                hint="Check POSTGRES_USER and POSTGRES_PASSWORD in your environment file. "
                "Ensure the database user exists and password is correct.",
            )
        raise


async def _initialize_cache(app: Application) -> None:
    """Initialize Redis cache connection during startup."""
    cache_service = app.infrastructure_container.cache_service()
    await cache_service._ensure_connected()


# =============================================================================
# Lifespan Management
# =============================================================================


@asynccontextmanager
async def lifespan(app: Application) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.

    Handles:
    - Startup: Database, cache, and identity kit initialization
    - Shutdown: Graceful cleanup of all infrastructure resources

    The ASGI lifespan protocol ensures this runs on worker startup/shutdown.
    Uvicorn handles SIGTERM/SIGINT signals and triggers lifespan shutdown.
    """
    try:
        # Startup
        await _initialize_database(app)
        await _initialize_cache(app)

        # Initialize identity-plan-kit (uses shared session_factory)
        await app.identity_kit.startup()
        logger.info("Identity kit started")

        logger.info(
            "Application started",
            app_name=SETTINGS.APP.APP_NAME,
            environment=SETTINGS.APP.ENVIRONMENT,
        )
        yield

    finally:
        # Shutdown
        logger.info("Starting graceful shutdown")

        # Shutdown identity-plan-kit first
        try:
            await app.identity_kit.shutdown()
            logger.info("Identity kit shutdown completed")
        except Exception:
            logger.exception("Error during identity kit shutdown")

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


def _init_identity_kit(app: Application) -> None:
    """Initialize identity-plan-kit with shared database session factory."""
    # Get the session_factory from the database adapter
    session_factory = app.infrastructure_container.pg_db().session_factory

    # Create identity kit with shared session factory
    app.identity_kit = create_identity_kit(session_factory)

    logger.debug("Identity kit initialized with shared session factory")


def _init_containers(app: Application) -> None:
    """Initialize all dependency injection containers."""
    _init_infrastructure_container(app)
    _init_system_container(app)
    _init_identity_kit(app)


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

    # HTTP exceptions (404, 405, etc.)
    app.add_exception_handler(HTTPException, http_exception_handler)

    # Domain exceptions (business rule violations)
    app.add_exception_handler(DomainError, domain_exception_handler)

    # Identity Plan Kit exceptions (auth, RBAC, plans)
    # Register specific handlers before base handlers for proper exception hierarchy
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["TokenExpiredError"], token_expired_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["TokenInvalidError"], token_invalid_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["UserInactiveError"], user_inactive_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["IPKUserNotFoundError"], ipk_user_not_found_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["AuthError"], auth_error_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["PermissionDeniedError"], permission_denied_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["RoleNotFoundError"], role_not_found_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["QuotaExceededError"], quota_exceeded_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["PlanExpiredError"], plan_expired_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["FeatureNotAvailableError"], feature_not_available_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["UserPlanNotFoundError"], user_plan_not_found_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["PlanNotFoundError"], plan_not_found_handler)
    app.add_exception_handler(IPK_EXCEPTION_HANDLERS["IPKBaseError"], ipk_base_error_handler)

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


def _setup_identity_kit(app: Application) -> None:
    """Setup identity-plan-kit routes and middleware.

    Registers:
    - Auth routes: /auth/google, /auth/google/callback, /auth/refresh, /auth/logout

    Note:
    - Health routes are disabled as we use our own at /api/v1/system/*
    - Error handlers are disabled - we use our own handlers in _configure_exception_handlers
      to maintain consistent error response format across the entire API
    """
    app.identity_kit.setup(
        app,
        register_error_handlers=False,  # Use our handlers for consistent error format
        include_health_routes=False,  # We have our own health endpoints
        include_request_id=False,  # We have our own RequestIdMiddleware
    )

    logger.debug("Identity kit routes registered")


def _setup_admin_panel(app: Application) -> None:
    """Setup admin panel with identity-plan-kit models.

    Provides:
    - User management (view, edit, delete users)
    - OAuth provider links
    - Refresh token management
    - Role and permission management (RBAC)
    - Subscription plan management
    - Feature usage tracking

    Admin panel is available at /admin with password authentication.
    """
    # Get the database engine from infrastructure container
    engine = app.infrastructure_container.pg_db().engine

    # Setup admin panel with all identity-plan-kit views
    setup_admin_panel(
        app,
        engine,
        base_url="/admin",
        title="Admin Panel",
    )

    logger.debug("Admin panel configured at /admin")


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
        description="Production ready API",
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
    _setup_identity_kit(app)  # Auth routes at /auth/*
    _setup_admin_panel(app)  # Admin panel at /admin
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
