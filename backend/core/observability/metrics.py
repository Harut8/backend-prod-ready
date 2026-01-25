"""
Prometheus Metrics for FastAPI.

Provides application metrics for monitoring:
- HTTP request latency histograms
- Request counts by method/path/status
- Active request gauges
- Circuit breaker state gauges
- Database connection pool metrics
- Redis connection metrics

Usage:
    from backend.core.observability.metrics import setup_metrics

    # In main.py
    setup_metrics(app)
"""

from collections.abc import Callable
import re
import time
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import REGISTRY
import structlog

from backend.core.conf.settings import SETTINGS


logger = structlog.get_logger(__name__)

# Check if we're running in multiprocess mode (gunicorn with multiple workers)
# In multiprocess mode, we need to use a shared registry
try:
    import os

    if "prometheus_multiproc_dir" in os.environ:
        # Multiprocess mode - use shared registry
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        IS_MULTIPROCESS = True
    else:
        registry = REGISTRY
        IS_MULTIPROCESS = False
except ImportError:
    registry = REGISTRY
    IS_MULTIPROCESS = False


# HTTP Request Metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status_code"],
    registry=registry,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0),
    registry=registry,
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "path"],
    registry=registry,
)

# Circuit Breaker Metrics
circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half-open, 2=open)",
    ["name"],
    registry=registry,
)

circuit_breaker_failures_total = Counter(
    "circuit_breaker_failures_total",
    "Total number of circuit breaker failures",
    ["name"],
    registry=registry,
)

circuit_breaker_successes_total = Counter(
    "circuit_breaker_successes_total",
    "Total number of circuit breaker successes",
    ["name"],
    registry=registry,
)

# Database Metrics
db_connections_total = Gauge(
    "db_connections_total",
    "Total database connections in pool",
    registry=registry,
)

db_connections_available = Gauge(
    "db_connections_available",
    "Available database connections in pool",
    registry=registry,
)

db_connections_in_use = Gauge(
    "db_connections_in_use",
    "Database connections currently in use",
    registry=registry,
)

# Application Info
app_info = Gauge(
    "app_info",
    "Application information",
    ["version", "environment"],
    registry=registry,
)

# =============================================================================
# BUSINESS METRICS
# =============================================================================
# These metrics track business-level events and operations.
# They are separate from infrastructure metrics to provide business insights.

# User Registration Metrics
user_registrations_total = Counter(
    "user_registrations_total",
    "Total number of user registrations",
    ["method"],  # e.g., "google_oauth", "email", "magic_link"
    registry=registry,
)

# Authentication Metrics
auth_attempts_total = Counter(
    "auth_attempts_total",
    "Total number of authentication attempts",
    ["method", "result"],  # method: google, email; result: success, failure
    registry=registry,
)

active_sessions_total = Gauge(
    "active_sessions_total",
    "Number of active user sessions",
    registry=registry,
)

# API Usage Metrics
api_usage_total = Counter(
    "api_usage_total",
    "Total API calls by feature",
    ["feature", "operation"],  # feature: profiles, events, etc; operation: create, read, update, delete
    registry=registry,
)

# Cache Metrics
cache_hits_total = Counter(
    "cache_hits_total",
    "Total cache hits",
    ["cache_type"],  # e.g., "user", "session", "config"
    registry=registry,
)

cache_misses_total = Counter(
    "cache_misses_total",
    "Total cache misses",
    ["cache_type"],
    registry=registry,
)

cache_operation_failures_total = Counter(
    "cache_operation_failures_total",
    "Total cache operation failures (connection errors, timeouts, etc.)",
    ["operation"],  # e.g., "get", "set", "delete", "setnx"
    registry=registry,
)

# External API Call Metrics
external_api_calls_total = Counter(
    "external_api_calls_total",
    "Total calls to external APIs",
    ["service", "result"],  # service: telegram, etc; result: success, failure, timeout
    registry=registry,
)

external_api_latency_seconds = Histogram(
    "external_api_latency_seconds",
    "External API call latency in seconds",
    ["service"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)


def _normalize_path(path: str) -> str:
    """
    Normalize request path to avoid high cardinality metrics.

    Replaces dynamic path segments (UUIDs, IDs) with placeholders.
    """

    # Replace UUIDs
    path = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "{id}",
        path,
    )
    # Replace numeric IDs
    return re.sub(r"/\d+(/|$)", "/{id}\\1", path)


def create_metrics_middleware() -> Callable[[Request, Callable[..., Any]], Any]:
    """Create middleware for collecting HTTP metrics."""

    async def metrics_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
        """Middleware to collect HTTP request metrics."""
        method = request.method
        path = _normalize_path(request.url.path)

        # Skip metrics endpoint itself to avoid recursion
        if path in ("/metrics", "/api/v1/system/metrics"):
            result: Response = await call_next(request)
            return result

        # Track in-progress requests
        http_requests_in_progress.labels(method=method, path=path).inc()

        start_time = time.perf_counter()
        response: Response | None = None

        try:
            response = await call_next(request)
            return response  # noqa: RET504
        finally:
            # Record duration
            duration = time.perf_counter() - start_time
            http_request_duration_seconds.labels(method=method, path=path).observe(duration)

            # Record request count
            status_code = response.status_code if response else 500
            http_requests_total.labels(method=method, path=path, status_code=status_code).inc()

            # Decrement in-progress
            http_requests_in_progress.labels(method=method, path=path).dec()

    return metrics_middleware


def setup_metrics(app: FastAPI, *, endpoint: str | None = None) -> None:
    """
    Set up Prometheus metrics collection for FastAPI application.

    Only enabled if METRICS_ENABLED=true in environment/settings.

    Args:
        app: FastAPI application instance
        endpoint: Path for metrics endpoint (defaults to settings value)
    """

    if not SETTINGS.METRICS.METRICS_ENABLED:
        logger.info("Prometheus metrics disabled (METRICS_ENABLED=false)")
        return

    endpoint = endpoint or SETTINGS.METRICS.METRICS_ENDPOINT

    # Set application info
    app_info.labels(version="1.0.0", environment=SETTINGS.APP.ENVIRONMENT).set(1)

    # Add metrics middleware
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
        method = request.method
        path = _normalize_path(request.url.path)

        # Skip metrics endpoint itself
        if path in ("/metrics", "/api/v1/system/metrics"):
            result: Response = await call_next(request)
            return result

        http_requests_in_progress.labels(method=method, path=path).inc()
        start_time = time.perf_counter()
        response: Response | None = None

        try:
            response = await call_next(request)
            return response  # noqa: RET504
        finally:
            duration = time.perf_counter() - start_time
            http_request_duration_seconds.labels(method=method, path=path).observe(duration)

            status_code = response.status_code if response else 500
            http_requests_total.labels(method=method, path=path, status_code=status_code).inc()
            http_requests_in_progress.labels(method=method, path=path).dec()

    # Add metrics endpoint
    @app.get(endpoint, include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    logger.info("Prometheus metrics enabled", endpoint=endpoint)


def record_circuit_breaker_state(name: str, state: str) -> None:
    """
    Record circuit breaker state change.

    Args:
        name: Circuit breaker name
        state: State (closed, half-open, open)
    """
    state_value = {"closed": 0, "half-open": 1, "open": 2}.get(state.lower(), -1)
    circuit_breaker_state.labels(name=name).set(state_value)

    if state.lower() == "open":
        logger.warning("Circuit breaker opened", circuit_breaker=name)


def record_circuit_breaker_failure(name: str) -> None:
    """Record a circuit breaker failure."""
    circuit_breaker_failures_total.labels(name=name).inc()


def record_circuit_breaker_success(name: str) -> None:
    """Record a circuit breaker success."""
    circuit_breaker_successes_total.labels(name=name).inc()


def update_db_pool_metrics(total: int, available: int, in_use: int) -> None:
    """Update database connection pool metrics."""
    db_connections_total.set(total)
    db_connections_available.set(available)
    db_connections_in_use.set(in_use)


# =============================================================================
# BUSINESS METRIC HELPERS
# =============================================================================


def record_user_registration(method: str) -> None:
    """Record a user registration event.

    Args:
        method: Registration method (e.g., "google_oauth", "email", "magic_link")
    """
    user_registrations_total.labels(method=method).inc()


def record_auth_attempt(method: str, *, success: bool) -> None:
    """Record an authentication attempt.

    Args:
        method: Auth method (e.g., "google", "email", "token")
        success: Whether authentication succeeded
    """
    result = "success" if success else "failure"
    auth_attempts_total.labels(method=method, result=result).inc()


def update_active_sessions(count: int) -> None:
    """Update the active sessions gauge.

    Args:
        count: Current number of active sessions
    """
    active_sessions_total.set(count)


def record_api_usage(feature: str, operation: str) -> None:
    """Record API usage by feature and operation.

    Args:
        feature: Feature name (e.g., "profiles", "events", "system")
        operation: Operation type (e.g., "create", "read", "update", "delete")
    """
    api_usage_total.labels(feature=feature, operation=operation).inc()


def record_cache_access(cache_type: str, *, hit: bool) -> None:
    """Record a cache access (hit or miss).

    Args:
        cache_type: Type of cache (e.g., "user", "session", "config")
        hit: Whether the cache lookup was a hit
    """
    if hit:
        cache_hits_total.labels(cache_type=cache_type).inc()
    else:
        cache_misses_total.labels(cache_type=cache_type).inc()


def record_cache_operation_failure(operation: str) -> None:
    """Record a cache operation failure (distinguishes from cache misses).

    This metric helps distinguish between:
    - Cache misses (key not found - normal operation)
    - Cache failures (connection errors, timeouts, etc. - indicates problems)

    Args:
        operation: The operation that failed (e.g., "get", "set", "delete", "setnx")
    """
    cache_operation_failures_total.labels(operation=operation).inc()


def record_external_api_call(service: str, *, success: bool, latency_seconds: float | None = None) -> None:
    """Record an external API call.

    Args:
        service: External service name (e.g., "telegram", "google")
        success: Whether the call succeeded
        latency_seconds: Call latency in seconds (if available)
    """
    result = "success" if success else "failure"
    external_api_calls_total.labels(service=service, result=result).inc()

    if latency_seconds is not None:
        external_api_latency_seconds.labels(service=service).observe(latency_seconds)
