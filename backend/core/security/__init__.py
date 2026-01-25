"""
Security infrastructure for rate limiting, timeouts, and circuit breakers.
"""

from backend.core.security.circuit_breaker import (
    get_db_circuit_breaker_factory,
    type_preserving_circuit_breaker,
)
from backend.core.security.rate_limiting import (
    extract_client_ip,
    get_user_id_or_ip,
    limiter,
    rate_limit_exceeded_handler,
)
from backend.core.security.timeout import timeout, timeout_uow_aware


__all__ = [
    "extract_client_ip",
    # Circuit breaker
    "get_db_circuit_breaker_factory",
    "get_user_id_or_ip",
    # Rate limiting
    "limiter",
    "rate_limit_exceeded_handler",
    # Timeouts
    "timeout",
    "timeout_uow_aware",
    "type_preserving_circuit_breaker",
]
