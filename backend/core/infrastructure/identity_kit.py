"""
Identity Plan Kit Integration Module.

This module integrates the identity-plan-kit library with the application,
providing authentication (Google OAuth), RBAC, and subscription plan management.

The integration uses:
- Direct config: Map existing app settings to IPK configuration explicitly
- External session_factory: Share the existing database connection pool
- Disabled health routes: Use our existing health endpoints
"""

from typing import TYPE_CHECKING

from identity_plan_kit import Environment, IdentityPlanKit, IdentityPlanKitConfig
import structlog

from backend.core.conf.settings import SETTINGS


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


logger = structlog.get_logger(__name__)


def _get_environment() -> Environment:
    """Map app environment to IPK Environment enum."""
    env_map = {
        "local": Environment.DEVELOPMENT,
        "dev": Environment.DEVELOPMENT,
        "prod": Environment.PRODUCTION,
    }
    return env_map.get(SETTINGS.APP.ENVIRONMENT, Environment.DEVELOPMENT)


def _is_production() -> bool:
    """Check if running in production environment."""
    return SETTINGS.APP.ENVIRONMENT == "prod"


def create_identity_kit_config() -> IdentityPlanKitConfig:
    """Create IdentityPlanKitConfig using existing application settings.

    Maps all relevant settings from the project's configuration to IPK,
    ensuring consistency and avoiding duplication of environment variables.
    """
    config = IdentityPlanKitConfig(
        # =================================================================
        # Environment
        # =================================================================
        environment=_get_environment(),
        # =================================================================
        # Database (URL needed for validation, but we use external pool)
        # =================================================================
        database_url=str(SETTINGS.DATABASE.DATABASE_URL),
        database_echo=SETTINGS.APP.LOG_LEVEL == "DEBUG",
        database_pool_size=SETTINGS.DATABASE.DB_POOL_SIZE,
        # =================================================================
        # Security & JWT
        # =================================================================
        secret_key=SETTINGS.JWT.JWT_SECRET.get_secret_value(),
        algorithm=SETTINGS.JWT.JWT_ALGORITHM,
        # =================================================================
        # Token Expiration
        # =================================================================
        access_token_expire_minutes=SETTINGS.JWT.JWT_EXPIRE_MINUTES,
        refresh_token_expire_days=SETTINGS.JWT.JWT_REFRESH_EXPIRE_DAYS,
        # =================================================================
        # Google OAuth
        # =================================================================
        google_client_id=SETTINGS.GOOGLE.GOOGLE_OAUTH_CLIENT_ID.get_secret_value(),
        google_client_secret=SETTINGS.GOOGLE.GOOGLE_OAUTH_CLIENT_SECRET.get_secret_value(),
        google_redirect_uri=SETTINGS.GOOGLE.GOOGLE_OAUTH_REDIRECT_URI,
        # =================================================================
        # Redis (for distributed caching/sessions)
        # Only use Redis in production; local/dev uses in-memory store
        # =================================================================
        redis_url=SETTINGS.REDIS.REDIS_URL if _is_production() else None,
        require_redis=_is_production(),  # In-memory store OK for local/dev
        # =================================================================
        # Cookies (secure in production)
        # =================================================================
        cookie_secure=_is_production(),
        cookie_samesite="lax",
        # =================================================================
        # API Prefix (auth routes will be at /auth/*)
        # =================================================================
        api_prefix=SETTINGS.API.API_V1_PREFIX,
        auth_prefix="/auth",
        # =================================================================
        # Default Role & Plan for new users
        # =================================================================
        default_role_code="user",
        default_plan_code="free",
        # =================================================================
        # Graceful Shutdown
        # =================================================================
        shutdown_grace_period_seconds=SETTINGS.TIMEOUTS.SHUTDOWN_TIMEOUT,
        # =================================================================
        # Rate Limiting (per endpoint)
        # =================================================================
        rate_limit_login=f"{SETTINGS.RATE_LIMIT.RATE_LIMIT_REQUESTS_PER_MINUTE}/minute",
        rate_limit_callback=f"{SETTINGS.RATE_LIMIT.RATE_LIMIT_REQUESTS_PER_MINUTE // 6}/minute",
        rate_limit_refresh=f"{SETTINGS.RATE_LIMIT.RATE_LIMIT_REQUESTS_PER_MINUTE // 2}/minute",
        rate_limit_logout=f"{SETTINGS.RATE_LIMIT.RATE_LIMIT_REQUESTS_PER_MINUTE // 6}/minute",
        # =================================================================
        # Proxy Headers (trust when behind reverse proxy)
        # =================================================================
        trust_proxy_headers=True,  # We're behind nginx
        # =================================================================
        # Metrics (use our existing Prometheus setup)
        # =================================================================
        enable_metrics=False,  # Disabled - we have our own metrics
        # =================================================================
        # Usage Tracking
        # =================================================================
        enable_usage_tracking=True,
        # =================================================================
        # Logging
        # =================================================================
        log_level=SETTINGS.APP.LOG_LEVEL,
    )

    logger.info(
        "Identity kit config created",
        environment=config.environment.value,
        api_prefix=config.api_prefix,
        auth_prefix=config.auth_prefix,
        cookie_secure=config.cookie_secure,
        metrics_enabled=config.enable_metrics,
    )

    return config


def create_identity_kit(
    session_factory: "async_sessionmaker[AsyncSession]",
) -> IdentityPlanKit:
    """Create IdentityPlanKit instance using existing database pool.

    Args:
        session_factory: The existing SQLAlchemy async session factory
                        to share the database connection pool.

    Returns:
        Configured IdentityPlanKit instance ready to be set up with FastAPI.
    """
    config = create_identity_kit_config()

    # Pass our existing session_factory - IPK won't create its own pool
    kit = IdentityPlanKit(
        config,
        session_factory=session_factory,
        # Timeouts for startup/shutdown
        startup_timeout=30.0,
        shutdown_drain_timeout=SETTINGS.TIMEOUTS.SHUTDOWN_TIMEOUT,
    )

    logger.info(
        "Identity kit instance created",
        shared_session_factory=True,
        startup_timeout=30.0,
        shutdown_drain_timeout=SETTINGS.TIMEOUTS.SHUTDOWN_TIMEOUT,
    )

    return kit
