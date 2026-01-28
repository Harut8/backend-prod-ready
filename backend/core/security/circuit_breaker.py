from collections.abc import Awaitable, Callable
from enum import Enum
import functools
import random
import threading
from typing import Any, ClassVar, ParamSpec, Self, TypeVar

from purgatory import AsyncCircuitBreakerFactory
from purgatory.domain.model import OpenedState as CircuitBreakerOpen
import structlog

from backend.core.conf.settings import SETTINGS


P = ParamSpec("P")
R = TypeVar("R")

logger = structlog.get_logger(__name__)

# Jitter percentage for circuit breaker reset timeout (0.0 to 1.0)
# This prevents thundering herd when multiple circuit breakers reset simultaneously
CIRCUIT_BREAKER_JITTER_FACTOR = 0.2  # 20% jitter


class CircuitBreakerType(Enum):
    """Types of circuit breakers available in the system."""

    DATABASE = "database"
    REDIS = "redis"
    EXTERNAL_API = "external_api"  # For external services like Telegram, etc.


class CircuitBreakerRegistry:
    """Thread-safe registry for circuit breaker factory singletons.

    This class-based approach provides:
    - Explicit lifecycle management
    - Easy testing via reset()
    - State change observability
    - Clear ownership of circuit breaker instances
    """

    _instance: ClassVar[Self | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        self._factories: dict[CircuitBreakerType, AsyncCircuitBreakerFactory] = {}
        self._lock = threading.Lock()
        # Track circuit breaker states for observability
        self._states: dict[str, str] = {}  # breaker_name -> state

    @classmethod
    def get_instance(cls) -> Self:
        """Get the singleton registry instance (thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the registry for testing purposes.

        This clears all circuit breaker factories, allowing tests to start fresh.
        """
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._factories.clear()
                cls._instance._states.clear()
            cls._instance = None
        logger.debug("Circuit breaker registry reset")

    @staticmethod
    def _apply_jitter(base_timeout: int) -> int:
        """Apply random jitter to timeout to prevent thundering herd.

        When multiple circuit breakers reset at the same time, they can all
        attempt to reconnect simultaneously, causing a spike that re-triggers
        the circuit breaker. Adding jitter spreads out the reset attempts.

        Args:
            base_timeout: The base reset timeout in seconds

        Returns:
            Timeout with random jitter applied (always >= base_timeout)
        """
        jitter = random.uniform(0, CIRCUIT_BREAKER_JITTER_FACTOR) * base_timeout
        return int(base_timeout + jitter)

    def _get_config(self, _cb_type: CircuitBreakerType) -> tuple[int, int, str]:
        """Get configuration for a circuit breaker type.

        Returns:
            Tuple of (failure_threshold, reset_timeout_with_jitter, log_name)

        Note:
            Reset timeout includes random jitter (0-20%) to prevent thundering herd
            when multiple circuit breakers reset simultaneously.
        """
        if _cb_type == CircuitBreakerType.DATABASE:
            return (
                SETTINGS.DATABASE.DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                self._apply_jitter(SETTINGS.DATABASE.DB_CIRCUIT_BREAKER_RESET_TIMEOUT),
                "Database",
            )
        if _cb_type == CircuitBreakerType.EXTERNAL_API:
            return (
                SETTINGS.EXTERNAL_API.EXTERNAL_API_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                self._apply_jitter(SETTINGS.EXTERNAL_API.EXTERNAL_API_CIRCUIT_BREAKER_RESET_TIMEOUT),
                "External API",
            )
        # CircuitBreakerType.REDIS
        return (
            SETTINGS.REDIS.REDIS_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            self._apply_jitter(SETTINGS.REDIS.REDIS_CIRCUIT_BREAKER_RESET_TIMEOUT),
            "Redis",
        )

    def get_factory(self, _cb_type: CircuitBreakerType) -> AsyncCircuitBreakerFactory:
        """Get or create a circuit breaker factory (thread-safe).

        Uses double-checked locking pattern for thread-safe lazy initialization.
        """
        if _cb_type in self._factories:
            return self._factories[_cb_type]

        with self._lock:
            # Double-check after acquiring lock
            if _cb_type not in self._factories:
                _failure_threshold, _reset_timeout, _log_name = self._get_config(_cb_type)

                self._factories[_cb_type] = AsyncCircuitBreakerFactory(
                    default_threshold=_failure_threshold,
                    default_ttl=_reset_timeout,
                )

                logger.info(
                    "Circuit breaker factory initialized",
                    circuit_type=_cb_type.value,
                    log_name=_log_name,
                    failure_threshold=_failure_threshold,
                    reset_timeout_seconds=_reset_timeout,
                )

        return self._factories[_cb_type]

    def log_circuit_open(self, _breaker_name: str, _cb_type: CircuitBreakerType) -> None:
        """Log when a circuit breaker opens due to failures.

        This should be called when CircuitBreakerOpen is raised.
        """
        _previous_state = self._states.get(_breaker_name, "closed")
        self._states[_breaker_name] = "open"

        if _previous_state != "open":
            logger.warning(
                "Circuit breaker OPENED - too many failures",
                breaker_name=_breaker_name,
                circuit_type=_cb_type.value,
                previous_state=_previous_state,
                new_state="open",
            )

    def log_circuit_success(self, _breaker_name: str, _cb_type: CircuitBreakerType) -> None:
        """Log when a circuit breaker succeeds (potentially recovering from open state)."""
        _previous_state = self._states.get(_breaker_name, "closed")

        if _previous_state == "open":
            self._states[_breaker_name] = "closed"
            logger.info(
                "Circuit breaker CLOSED - recovered after success",
                breaker_name=_breaker_name,
                circuit_type=_cb_type.value,
                previous_state=_previous_state,
                new_state="closed",
            )

    def log_circuit_failure(self, _breaker_name: str, _cb_type: CircuitBreakerType, _error: str) -> None:
        """Log when a circuit breaker records a failure."""
        logger.debug(
            "Circuit breaker recorded failure",
            breaker_name=_breaker_name,
            circuit_type=_cb_type.value,
            error=_error,
        )


def _get_registry() -> CircuitBreakerRegistry:
    """Get the circuit breaker registry singleton."""
    return CircuitBreakerRegistry.get_instance()


def get_db_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get or create the database circuit breaker factory singleton (thread-safe)."""
    return _get_registry().get_factory(CircuitBreakerType.DATABASE)


def get_redis_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get or create the Redis circuit breaker factory singleton (thread-safe).

    Prevents cascading latency when Redis becomes slow or unresponsive.
    """
    return _get_registry().get_factory(CircuitBreakerType.REDIS)


def get_external_api_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get or create the external API circuit breaker factory singleton (thread-safe).

    Prevents cascading failures when external APIs (Telegram, etc.) are slow or unavailable.
    Uses more conservative thresholds than internal services since external APIs
    are less reliable and failures are more expected.
    """
    return _get_registry().get_factory(CircuitBreakerType.EXTERNAL_API)


def get_telegram_circuit_breaker_factory() -> AsyncCircuitBreakerFactory:
    """Get circuit breaker factory for Telegram API.

    Uses the external API circuit breaker with appropriate thresholds
    for Telegram notification service.
    """
    return get_external_api_circuit_breaker_factory()


def type_preserving_circuit_breaker(
    _circuit_breaker_decorator: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap circuit breaker decorator to preserve type information."""

    def decorator(_func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        _wrapped = _circuit_breaker_decorator(_func)

        @functools.wraps(_func)
        async def _type_preserved_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return await _wrapped(*args, **kwargs)  # type: ignore[no-any-return]

        return _type_preserved_wrapper

    return decorator


def observable_circuit_breaker(
    _cb_type: CircuitBreakerType,
    _breaker_name: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Create an observable circuit breaker decorator with state change logging.

    This wrapper adds observability to the circuit breaker by logging:
    - When the circuit opens (too many failures)
    - When the circuit closes (successful recovery)
    - Individual failures (at debug level)

    Args:
        _cb_type: The type of circuit breaker to use
        _breaker_name: A unique name for this circuit breaker instance

    Returns:
        A decorator that wraps async functions with circuit breaker protection
        and state change logging.
    """
    _factory = _get_registry().get_factory(_cb_type)
    _registry = _get_registry()

    def decorator(_func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(_func)
        async def _wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            async with _factory.get_breaker(_breaker_name):
                try:
                    _result = await _func(*args, **kwargs)
                    _registry.log_circuit_success(_breaker_name, _cb_type)
                    return _result
                except CircuitBreakerOpen:
                    _registry.log_circuit_open(_breaker_name, _cb_type)
                    raise
                except Exception as e:
                    _registry.log_circuit_failure(_breaker_name, _cb_type, str(e))
                    raise

        return _wrapper

    return decorator
