"""
Security infrastructure for rate limiting, timeouts, and circuit breakers.
"""

from backend.core.security.circuit_breaker import (
    CircuitBreakerRegistry,
    CircuitBreakerType,
    get_db_circuit_breaker_factory,
    get_external_api_circuit_breaker_factory,
    get_redis_circuit_breaker_factory,
    get_telegram_circuit_breaker_factory,
    observable_circuit_breaker,
    type_preserving_circuit_breaker,
)
from backend.core.security.proxy_validation import (
    ConfigurationError,
    extract_client_ip_secure,
    is_trusted_proxy,
)
from backend.core.security.rate_limiting import (
    extract_client_ip,
    get_user_id_or_ip,
    limiter,
    rate_limit_exceeded_handler,
)
from backend.core.security.timeout import timeout, timeout_uow_aware


__all__ = [
    # Circuit breaker
    "CircuitBreakerRegistry",
    "CircuitBreakerType",
    "get_db_circuit_breaker_factory",
    "get_external_api_circuit_breaker_factory",
    "get_redis_circuit_breaker_factory",
    "get_telegram_circuit_breaker_factory",
    "observable_circuit_breaker",
    "type_preserving_circuit_breaker",
    # Proxy validation
    "ConfigurationError",
    "extract_client_ip_secure",
    "is_trusted_proxy",
    # Rate limiting
    "extract_client_ip",
    "get_user_id_or_ip",
    "limiter",
    "rate_limit_exceeded_handler",
    # Timeouts
    "timeout",
    "timeout_uow_aware",
]
