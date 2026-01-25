from collections.abc import Awaitable, Callable
from enum import Enum
import functools
import threading
from typing import Any, ParamSpec, TypeVar

from purgatory import AsyncCircuitBreakerFactory
import structlog

from backend.core.conf.settings import SETTINGS


P = ParamSpec("P")
R = TypeVar("R")

logger = structlog.get_logger(__name__)


class CircuitBreakerType(Enum):
    """Types of circuit breakers available in the system."""

    DATABASE = "database"
    REDIS = "redis"
    EXTERNAL_API = "external_api"  # For external services like Telegram, etc.


# Storage for circuit breaker factory singletons
_circuit_breaker_factories: dict[CircuitBreakerType, AsyncCircuitBreakerFactory] = {}

# threading.Lock is used here (instead of asyncio.Lock) because:
# 1. The singleton initialization happens at module import time, which may occur
#    outside an async context where asyncio.Lock would fail
# 2. The lock is only held briefly during factory creation (not during I/O)
# 3. This follows the double-checked locking pattern for thread-safe lazy init
# 4. Once initialized, the factory is never modified, so contention is minimal
_circuit_breaker_lock = threading.Lock()


def _get_circuit_breaker_config(cb_type: CircuitBreakerType) -> tuple[int, int, str]:
    """Get configuration for a circuit breaker type.

    Returns:
        Tuple of (failure_threshold, reset_timeout, log_name)
    """
    if cb_type == CircuitBreakerType.DATABASE:
        return (
            SETTINGS.DATABASE.DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            SETTINGS.DATABASE.DB_CIRCUIT_BREAKER_RESET_TIMEOUT,
            "Database",
        )
    if cb_type == CircuitBreakerType.EXTERNAL_API:
        # More conservative thresholds for external APIs since they are
        # less reliable and failures are more expected
        return (
            5,  # Open after 5 consecutive failures
            60,  # Wait 60 seconds before attempting recovery
            "External API",
        )
    # CircuitBreakerType.REDIS
    return (
        SETTINGS.REDIS.REDIS_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        SETTINGS.REDIS.REDIS_CIRCUIT_BREAKER_RESET_TIMEOUT,
        "Redis",
    )


def _get_circuit_breaker_factory(cb_type: CircuitBreakerType) -> AsyncCircuitBreakerFactory:
    """Get or create a circuit breaker factory singleton (thread-safe).

    Uses double-checked locking pattern for thread-safe lazy initialization.
    """
    if cb_type in _circuit_breaker_factories:
        return _circuit_breaker_factories[cb_type]

    with _circuit_breaker_lock:
        # Double-check after acquiring lock
        if cb_type not in _circuit_breaker_factories:
            failure_threshold, reset_timeout, log_name = _get_circuit_breaker_config(cb_type)

            _circuit_breaker_factories[cb_type] = AsyncCircuitBreakerFactory(
                default_threshold=failure_threshold,
                default_ttl=reset_timeout,
            )

            logger.info(
                "%s circuit breaker initialized",
                log_name,
                failure_threshold=failure_threshold,
                reset_timeout=reset_timeout,
            )

    return _circuit_breaker_factories[cb_type]


def get_db_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get or create the database circuit breaker factory singleton (thread-safe)."""
    return _get_circuit_breaker_factory(CircuitBreakerType.DATABASE)


def get_redis_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get or create the Redis circuit breaker factory singleton (thread-safe).

    Prevents cascading latency when Redis becomes slow or unresponsive.
    """
    return _get_circuit_breaker_factory(CircuitBreakerType.REDIS)


def get_external_api_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get or create the external API circuit breaker factory singleton (thread-safe).

    Prevents cascading failures when external APIs (Telegram, etc.) are slow or unavailable.
    Uses more conservative thresholds than internal services since external APIs
    are less reliable and failures are more expected.
    """
    return _get_circuit_breaker_factory(CircuitBreakerType.EXTERNAL_API)


def get_telegram_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get circuit breaker factory for Telegram API.

    Uses the external API circuit breaker with appropriate thresholds
    for Telegram notification service.
    """
    return get_external_api_circuit_breaker_factory()


def type_preserving_circuit_breaker(
    circuit_breaker_decorator: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap circuit breaker decorator to preserve type information."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        wrapped = circuit_breaker_decorator(func)

        @functools.wraps(func)
        async def type_preserved_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return await wrapped(*args, **kwargs)  # type: ignore[no-any-return]

        return type_preserved_wrapper

    return decorator
