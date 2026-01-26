from functools import lru_cache
import os
from pathlib import Path
import re
import secrets
import string
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import structlog


# Determine which environment file to use based on ENV_STAGE environment variable
# Default to local if not specified
ENV_STAGE = os.getenv("ENV_STAGE", "local")
env_file_mapping = {"local": ".env.local", "dev": ".env.dev", "prod": ".env.prod"}

# Use the appropriate env file based on the environment
env_file_name = env_file_mapping.get(ENV_STAGE, ".env.local")
env_file = Path(__file__).parent / f"envs/{env_file_name}"

encoding = "utf-8"
case_sensitive = True

# Load environment variables from the specific env file
load_dotenv(env_file)

# Note: Logging configuration happens in main.py via configure_logging()
# We don't log here because structlog isn't configured yet at module import time


def generate_secret(byte: int = 512) -> str:
    return secrets.token_urlsafe(byte)


def generate_aes_key(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for i in range(length))


SECRET_KEY_32 = f"{generate_secret(32)}"
SECRET_KEY_64 = f"{generate_secret(64)}"
SECRET_KEY_32_AES = f"{generate_aes_key(32)}"


class CustomSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding=encoding,
        case_sensitive=case_sensitive,
        extra="ignore",
    )


class AppSettings(CustomSettings):
    APP_NAME: str = Field(default="Prod Ready Backend API", alias="APP_NAME")
    APP_VERSION: str = Field(default="1.0.0", alias="APP_VERSION")
    ENVIRONMENT: Literal["local", "dev", "prod"] = Field(default="local", alias="ENVIRONMENT")
    LOG_LEVEL: str = Field(default="INFO", alias="LOG_LEVEL")
    LOGGER_NAME: str = Field(default="prod-ready-backend", alias="LOGGER_NAME")
    JSON_LOGS: bool = Field(default=False, alias="JSON_LOGS")
    INCLUDE_TRACE_CONTEXT: bool = Field(default=False, alias="INCLUDE_TRACE_CONTEXT")
    CORS_ORIGINS: list[str] = Field(
        default=[
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:5173",
        ],
        alias="CORS_ORIGINS",
    )
    # Admission control: max concurrent requests before load shedding
    MAX_CONCURRENT_REQUESTS: int = Field(default=100, alias="MAX_CONCURRENT_REQUESTS")

    @model_validator(mode="before")
    @classmethod
    def set_json_logs_default(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Set JSON_LOGS default based on environment if not explicitly set."""
        # Check if JSON_LOGS was explicitly set in environment or data
        if "JSON_LOGS" not in data:
            # Not explicitly set, use environment-based default
            # JSON for prod, text (False) for local/dev
            environment = data.get("ENVIRONMENT", os.getenv("ENVIRONMENT", "local"))
            if environment == "prod":
                data["JSON_LOGS"] = True
            else:
                data["JSON_LOGS"] = False
        return data


class PgDbSettings(AppSettings):
    POSTGRES_ENGINE: str = Field(default="postgresql+asyncpg", alias="POSTGRES_ENGINE")
    POSTGRES_USER: str = Field(default="prod_user", alias="POSTGRES_USER")
    POSTGRES_PASSWORD: SecretStr = Field(
        default_factory=lambda: SecretStr("prod_password"), alias="POSTGRES_PASSWORD"
    )
    POSTGRES_DB: str = Field(default="prod_ready_db", alias="POSTGRES_DB")
    POSTGRES_HOST: str = Field(default="postgres", alias="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(default=5432, alias="POSTGRES_PORT")
    DATABASE_URL: PostgresDsn | str = Field(default="", alias="DATABASE_URL")

    # Connection pool settings
    DB_POOL_SIZE: int = Field(
        default=10,
        alias="DB_POOL_SIZE",
        description="Number of connections to keep in the pool",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=20,
        alias="DB_MAX_OVERFLOW",
        description="Max additional connections when pool is exhausted",
    )
    DB_POOL_TIMEOUT: int = Field(
        default=30,
        alias="DB_POOL_TIMEOUT",
        description="Seconds to wait for a connection from the pool",
    )
    DB_POOL_RECYCLE: int = Field(
        default=1800,
        alias="DB_POOL_RECYCLE",
        description="Seconds after which a connection is recycled (30 min default)",
    )

    # Circuit breaker settings for database operations
    DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(
        default=20,
        alias="DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
        description="Consecutive database failures before opening circuit breaker",
    )
    DB_CIRCUIT_BREAKER_RESET_TIMEOUT: int = Field(
        default=10,
        alias="DB_CIRCUIT_BREAKER_RESET_TIMEOUT",
        description="Seconds to wait before attempting to close circuit breaker (half-open state)",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_postgres_dsn(cls, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("DATABASE_URL"):
            # Use environment variables or defaults
            host = data.get("POSTGRES_HOST", "localhost")
            _built_uri = PostgresDsn.build(
                scheme=data.setdefault("POSTGRES_ENGINE", "postgresql+asyncpg"),
                username=data.setdefault("POSTGRES_USER", "prod_user"),
                password=data.setdefault("POSTGRES_PASSWORD", "prod_password"),
                host=host,
                port=int(data.setdefault("POSTGRES_PORT", 5432)),
                path=data.setdefault("POSTGRES_DB", "prod_ready_db"),
            ).unicode_string()
            data["DATABASE_URL"] = _built_uri
        return data


class ApiSettings(CustomSettings):
    API_V1_PREFIX: str = "/api/v1"
    API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(SECRET_KEY_32))
    API_SECRET: SecretStr = Field(default_factory=lambda: SecretStr(SECRET_KEY_32))
    INTERNAL_CALL_API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(SECRET_KEY_32))


class JWTSettings(CustomSettings):
    JWT_SECRET: SecretStr = Field(default_factory=lambda: SecretStr(SECRET_KEY_64), alias="JWT_SECRET")
    JWT_ALGORITHM: str = Field(default="HS256", alias="JWT_ALGORITHM")
    JWT_EXPIRE_MINUTES: int = Field(default=15, alias="JWT_EXPIRE_MINUTES")
    JWT_REFRESH_EXPIRE_DAYS: int = Field(
        default=30, alias="JWT_REFRESH_EXPIRE_DAYS", description="Refresh token expiry in days"
    )
    AUDIENCE: SecretStr = Field(default_factory=lambda: SecretStr("prb-frontend"), alias="JWT_AUDIENCE")
    ISSUER: SecretStr = Field(default_factory=lambda: SecretStr("prb-api"), alias="JWT_ISSUER")

    # Refresh token rotation settings
    REFRESH_TOKEN_ROTATION_ENABLED: bool = Field(
        default=True,
        alias="REFRESH_TOKEN_ROTATION_ENABLED",
        description="Enable refresh token rotation for enhanced security",
    )
    REFRESH_TOKEN_CLEANUP_DAYS: int = Field(
        default=90,
        alias="REFRESH_TOKEN_CLEANUP_DAYS",
        description="Delete expired refresh tokens after this many days",
    )


class GoogleOAuthSettings(CustomSettings):
    GOOGLE_OAUTH_CLIENT_ID: SecretStr = Field(
        default_factory=lambda: SecretStr(SECRET_KEY_32), alias="GOOGLE_OAUTH_CLIENT_ID"
    )
    GOOGLE_OAUTH_CLIENT_SECRET: SecretStr = Field(
        default_factory=lambda: SecretStr(SECRET_KEY_32), alias="GOOGLE_OAUTH_CLIENT_SECRET"
    )
    GOOGLE_OAUTH_REDIRECT_URI: str = Field(
        default="http://localhost:8000/auth/google/callback",
        alias="GOOGLE_OAUTH_REDIRECT_URI",
        description="OAuth callback URL for Google authentication",
    )


class CacheSettings(CustomSettings):
    USER_CACHE_TTL_MINUTES: int = Field(default=30, alias="USER_CACHE_TTL_MINUTES")


class ExternalAPISettings(CustomSettings):
    """Settings for external API integrations (Telegram, payment providers, etc.)."""

    # Circuit breaker settings for external API calls
    EXTERNAL_API_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(
        default=5,
        alias="EXTERNAL_API_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
        description="Consecutive external API failures before opening circuit breaker",
    )
    EXTERNAL_API_CIRCUIT_BREAKER_RESET_TIMEOUT: int = Field(
        default=60,
        alias="EXTERNAL_API_CIRCUIT_BREAKER_RESET_TIMEOUT",
        description="Seconds to wait before attempting to close circuit breaker (half-open state)",
    )


class RateLimitSettings(CustomSettings):
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = Field(default=60, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")
    RATE_LIMIT_ENABLED: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")  # Disable for load testing

    # Trusted proxy CIDR ranges for secure X-Forwarded-For handling
    # These are the IP ranges of your reverse proxies (nginx, AWS ALB, Cloudflare, etc.)
    # Only requests from these IPs will have their X-Forwarded-For headers trusted
    #
    # SECURITY WARNING: The default ranges include broad private networks for development.
    # For production deployments, you SHOULD narrow these to your specific infrastructure:
    #
    # Example configurations by deployment type:
    #
    # AWS ALB only:
    #   TRUSTED_PROXY_CIDRS=["10.0.0.0/16"]  # Your VPC CIDR only
    #
    # AWS ALB + Cloudflare:
    #   TRUSTED_PROXY_CIDRS=["10.0.0.0/16", "173.245.48.0/20", "103.21.244.0/22", ...]
    #
    # Kubernetes (internal):
    #   TRUSTED_PROXY_CIDRS=["10.244.0.0/16"]  # Your pod CIDR only
    #
    # Direct nginx (no proxy):
    #   TRUSTED_PROXY_CIDRS=["127.0.0.1/32"]  # Localhost only
    #
    TRUSTED_PROXY_CIDRS: list[str] = Field(
        default=[
            # Localhost (for development)
            "127.0.0.0/8",
            "::1/128",
            # Docker default bridge network
            "172.17.0.0/16",
            # Docker compose networks
            "172.18.0.0/16",
            "172.19.0.0/16",
            # Private networks (commonly used for internal load balancers)
            # WARNING: These are broad defaults - narrow for production!
            "10.0.0.0/8",
            "192.168.0.0/16",
            # AWS ALB health check IPs (VPC internal)
            # Add your specific ALB subnet CIDRs in production
            # Cloudflare IPs (uncomment if using Cloudflare)
            # See: https://www.cloudflare.com/ips/
            # "173.245.48.0/20",
            # "103.21.244.0/22",
            # "103.22.200.0/22",
            # "103.31.4.0/22",
            # "141.101.64.0/18",
            # "108.162.192.0/18",
            # "190.93.240.0/20",
            # "188.114.96.0/20",
            # "197.234.240.0/22",
            # "198.41.128.0/17",
            # "162.158.0.0/15",
            # "104.16.0.0/13",
            # "104.24.0.0/14",
            # "172.64.0.0/13",
            # "131.0.72.0/22",
        ],
        alias="TRUSTED_PROXY_CIDRS",
        description="CIDR ranges of trusted reverse proxies for X-Forwarded-For validation. Narrow for production!",
    )


class AdminSettings(CustomSettings):
    ADMIN_USERNAME: str = Field(default="admin", alias="ADMIN_USERNAME")
    ADMIN_PASSWORD: SecretStr = Field(
        alias="ADMIN_PASSWORD",
        description="Admin password - must be explicitly set (no default for security)",
    )
    ADMIN_ALLOWED_IPS: list[str] = Field(
        default=["127.0.0.1", "::1", "192.168.65.1", "172.17.0.1", "10.0.0.1"],
        alias="ADMIN_ALLOWED_IPS",
    )
    ADMIN_SESSION_SECRET: SecretStr = Field(
        default_factory=lambda: SecretStr(SECRET_KEY_64),
        alias="ADMIN_SESSION_SECRET",
    )
    ADMIN_SESSION_TIMEOUT_MINUTES: int = Field(default=30, alias="ADMIN_SESSION_TIMEOUT_MINUTES")

    # MFA settings for admin users accessing billing/payment data
    MFA_ENABLED: bool = Field(
        default=False,
        alias="ADMIN_MFA_ENABLED",
        description="Enable TOTP-based MFA for admin authentication (recommended for production)",
    )
    MFA_SECRET: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        alias="ADMIN_MFA_SECRET",
        description="Base32-encoded TOTP secret for admin MFA (generate with: pyotp.random_base32())",
    )
    MFA_ISSUER_NAME: str = Field(
        default="PRB Admin",
        alias="ADMIN_MFA_ISSUER_NAME",
        description="Issuer name shown in authenticator apps (e.g., Google Authenticator)",
    )
    MFA_TOKEN_VALIDITY_SECONDS: int = Field(
        default=30,
        alias="ADMIN_MFA_TOKEN_VALIDITY_SECONDS",
        description="TOTP token validity window in seconds (standard: 30)",
    )


class GeoIPSettings(AppSettings):
    GEOIP_DB_PATH: str = Field(
        default=str(Path(__file__).parent.parent.parent.parent / "cities.mmdb"),
        alias="GEOIP_DB_PATH",
    )


class RedisSettings(AppSettings):
    REDIS_HOST: str = Field(default="localhost", alias="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, alias="REDIS_PORT")
    REDIS_USER: str = Field(default="", alias="REDIS_USER")
    REDIS_PASSWORD: SecretStr = Field(default_factory=lambda: SecretStr(""), alias="REDIS_PASSWORD")
    REDIS_DB: int = Field(default=0, alias="REDIS_DB")
    REDIS_URL: str = Field(default="", alias="REDIS_URL")

    # Circuit breaker settings for Redis operations
    REDIS_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(
        default=10,
        alias="REDIS_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
        description="Consecutive Redis failures before opening circuit breaker",
    )
    REDIS_CIRCUIT_BREAKER_RESET_TIMEOUT: int = Field(
        default=30,
        alias="REDIS_CIRCUIT_BREAKER_RESET_TIMEOUT",
        description="Seconds to wait before attempting to close circuit breaker (half-open state)",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_redis_url(cls, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("REDIS_URL"):
            scheme = "redis" if data.setdefault("ENVIRONMENT", "local") != "prod" else "rediss"
            _built_uri = RedisDsn.build(
                scheme=scheme,
                host=data.setdefault("REDIS_HOST", "redis"),
                port=int(data.setdefault("REDIS_PORT", 6379)),
                username=data.setdefault("REDIS_USER", "") or None,
                password=data.setdefault("REDIS_PASSWORD", "") or None,
                path=f"/{data.setdefault('REDIS_DB', 0)}",
            )
            data["REDIS_URL"] = str(_built_uri.unicode_string())
        return data


class ProfilingSettings(CustomSettings):
    """Configuration for production-ready profiling middleware."""

    PROFILING_ENABLED: bool = Field(
        default=False,
        alias="PROFILING_ENABLED",
        description="Enable/disable profiling middleware",
    )
    PROFILE_SAMPLE_RATE: float = Field(
        default=0.001,
        alias="PROFILE_SAMPLE_RATE",
        description="Random sampling rate 0.0-1.0 (default: 0.001 = 0.1%)",
    )
    REQUIRE_PROFILE_HEADER: bool = Field(
        default=False,
        alias="REQUIRE_PROFILE_HEADER",
        description="Require X-Profile-Token header for profiling",
    )
    PROFILE_HEADER_TOKEN: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        alias="PROFILE_HEADER_TOKEN",
        description="Secret token for header-based profiling authorization",
    )
    PROFILED_ENDPOINTS: list[str] = Field(
        default_factory=list,
        alias="PROFILED_ENDPOINTS",
        description="Specific endpoints to profile (empty list = all endpoints)",
    )
    PROFILE_RETENTION_DAYS: int = Field(
        default=7,
        alias="PROFILE_RETENTION_DAYS",
        description="Days to keep profile files before auto-cleanup",
    )
    PROFILE_INTERVAL_MS: float = Field(
        default=1.0,
        alias="PROFILE_INTERVAL_MS",
        description="Sampling interval in milliseconds for Pyinstrument",
    )


class MetricsSettings(CustomSettings):
    """Configuration for Prometheus metrics."""

    METRICS_ENABLED: bool = Field(
        default=False,
        alias="METRICS_ENABLED",
        description="Enable/disable Prometheus metrics endpoint",
    )
    METRICS_ENDPOINT: str = Field(
        default="/metrics",
        alias="METRICS_ENDPOINT",
        description="Path for Prometheus metrics endpoint",
    )
    METRICS_EXCLUDE_PATHS: list[str] = Field(
        default_factory=lambda: ["/health", "/metrics", "/ready", "/live"],
        alias="METRICS_EXCLUDE_PATHS",
        description="Paths to exclude from metrics collection",
    )


class TracingSettings(CustomSettings):
    """Configuration for OpenTelemetry distributed tracing."""

    TRACING_ENABLED: bool = Field(
        default=False,
        alias="TRACING_ENABLED",
        description="Enable/disable OpenTelemetry tracing",
    )
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(
        default="",
        alias="OTEL_EXPORTER_OTLP_ENDPOINT",
        description="OTLP collector endpoint (e.g., http://localhost:4317)",
    )
    OTEL_SERVICE_NAME: str = Field(
        default="",
        alias="OTEL_SERVICE_NAME",
        description="Service name for tracing (defaults to APP_NAME if empty)",
    )
    OTEL_TRACES_SAMPLER: str = Field(
        default="parentbased_traceidratio",
        alias="OTEL_TRACES_SAMPLER",
        description="Sampling strategy: always_on, always_off, parentbased_traceidratio",
    )
    OTEL_TRACES_SAMPLER_ARG: float = Field(
        default=0.1,
        alias="OTEL_TRACES_SAMPLER_ARG",
        description="Sampling ratio for traceidratio sampler (0.0-1.0, default 10%)",
    )
    OTEL_EXPORTER_OTLP_INSECURE: bool = Field(
        default=True,
        alias="OTEL_EXPORTER_OTLP_INSECURE",
        description="Use insecure connection to OTLP collector",
    )


class TimeoutSettings(CustomSettings):
    """Centralized timeout configuration for consistent behavior across the application.

    All timeout values are in seconds. Having these in one place allows for:
    - Easy tuning based on environment
    - Consistent timeout behavior
    - Clear documentation of timeout values
    """

    # Unit of Work context timeout (for entering async context)
    UOW_CONTEXT_TIMEOUT: float = Field(
        default=7.0,
        alias="UOW_CONTEXT_TIMEOUT",
        description="Timeout for UOW context initialization in seconds",
    )

    # Repository operation timeout
    REPOSITORY_TIMEOUT: float = Field(
        default=8.0,
        alias="REPOSITORY_TIMEOUT",
        description="Timeout for repository database operations in seconds",
    )

    # Transaction commit/flush timeout
    TRANSACTION_TIMEOUT: float = Field(
        default=10.0,
        alias="TRANSACTION_TIMEOUT",
        description="Timeout for transaction commit/flush operations in seconds",
    )

    # Session close timeout
    SESSION_CLOSE_TIMEOUT: float = Field(
        default=3.0,
        alias="SESSION_CLOSE_TIMEOUT",
        description="Timeout for closing database sessions in seconds",
    )

    # Graceful shutdown timeout
    SHUTDOWN_TIMEOUT: float = Field(
        default=10.0,
        alias="SHUTDOWN_TIMEOUT",
        description="Timeout for graceful shutdown in seconds",
    )

    # Asyncpg cleanup delay (for handling timeout scenarios)
    ASYNCPG_CLEANUP_DELAY: float = Field(
        default=0.1,
        alias="ASYNCPG_CLEANUP_DELAY",
        description="Delay before cleaning up after asyncpg timeout in seconds",
    )


class Settings(BaseModel):
    APP: AppSettings = Field(default_factory=AppSettings)
    DATABASE: PgDbSettings = Field(default_factory=PgDbSettings)
    API: ApiSettings = Field(default_factory=ApiSettings)
    JWT: JWTSettings = Field(default_factory=JWTSettings)
    GOOGLE: GoogleOAuthSettings = Field(default_factory=GoogleOAuthSettings)
    CACHE: CacheSettings = Field(default_factory=CacheSettings)
    REDIS: RedisSettings = Field(default_factory=RedisSettings)
    RATE_LIMIT: RateLimitSettings = Field(default_factory=RateLimitSettings)
    EXTERNAL_API: ExternalAPISettings = Field(default_factory=ExternalAPISettings)
    ADMIN: AdminSettings = Field(default_factory=AdminSettings)
    GEOIP: GeoIPSettings = Field(default_factory=GeoIPSettings)
    PROFILING: ProfilingSettings = Field(default_factory=ProfilingSettings)
    METRICS: MetricsSettings = Field(default_factory=MetricsSettings)
    TRACING: TracingSettings = Field(default_factory=TracingSettings)
    TIMEOUTS: TimeoutSettings = Field(default_factory=TimeoutSettings)

    @model_validator(mode="after")
    def validate_jwt_secret(self) -> "Settings":
        """Validate JWT secret - require explicit setting in production.

        Auto-generated secrets cause mass logouts on restart as all tokens
        become invalid. This is unacceptable in production.
        """
        _env_jwt_secret = os.getenv("JWT_SECRET")
        if self.APP.ENVIRONMENT == "prod" and not _env_jwt_secret:
            _msg = (
                "JWT_SECRET must be explicitly set in production environment. "
                "Auto-generated secrets invalidate all tokens on restart."
            )
            raise ValueError(_msg)
        if not _env_jwt_secret:
            logging.getLogger(__name__).warning(
                "JWT_SECRET not set - using auto-generated secret. "
                "This is acceptable for local/dev but will invalidate tokens on restart."
            )
        return self

    @model_validator(mode="after")
    def validate_admin_password(self) -> "Settings":
        """Enforce strong password policy for admin access in production."""
        # Only enforce strict validation in production environment
        if self.APP.ENVIRONMENT != "prod":
            # structlog is configured before settings.py is imported
            structlog.get_logger(__name__).warning(
                "Admin password validation skipped - not in production environment",
                environment=self.APP.ENVIRONMENT,
            )
            return self

        _password = self.ADMIN.ADMIN_PASSWORD.get_secret_value()

        # Minimum length requirement
        if len(_password) < 16:
            _msg = "Admin password must be at least 16 characters long for production"
            raise ValueError(_msg)

        # Complexity requirements
        _has_upper = bool(re.search(r"[A-Z]", _password))
        _has_lower = bool(re.search(r"[a-z]", _password))
        _has_digit = bool(re.search(r"\d", _password))
        _has_special = bool(re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\\/;~`]', _password))

        if not (_has_upper and _has_lower and _has_digit and _has_special):
            _msg = (
                "Admin password must contain at least one uppercase letter, "
                "one lowercase letter, one digit, and one special character"
            )
            raise ValueError(_msg)

        # Check for common weak passwords
        _weak_passwords = ["admin123", "password123", "Admin123456", "Password1234!"]
        if _password in _weak_passwords:
            _msg = "Admin password is too weak - please use a stronger password"
            raise ValueError(_msg)

        return self

    @model_validator(mode="after")
    def validate_mfa_settings(self) -> "Settings":
        """Enforce MFA in production for admin panel security.

        Admin panel accesses sensitive billing/payment data and must
        be protected with multi-factor authentication in production.
        """
        if self.APP.ENVIRONMENT == "prod" and not self.ADMIN.MFA_ENABLED:
            _msg = (
                "MFA must be enabled in production for admin panel. "
                "Set ADMIN_MFA_ENABLED=true and configure ADMIN_MFA_SECRET."
            )
            raise ValueError(_msg)
        if self.ADMIN.MFA_ENABLED and not self.ADMIN.MFA_SECRET.get_secret_value():
            _msg = (
                "ADMIN_MFA_SECRET must be set when MFA is enabled. "
                "Generate with: python -c 'import pyotp; print(pyotp.random_base32())'"
            )
            raise ValueError(_msg)
        return self

    @model_validator(mode="after")
    def validate_cors_origins(self) -> "Settings":
        """Validate CORS origins and warn if not set."""
        if not self.APP.CORS_ORIGINS and self.APP.ENVIRONMENT == "prod":
            _msg = "CORS_ORIGINS not set - using default origins"
            raise ValueError(_msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Global settings instance
SETTINGS: Settings = get_settings()
