"""
Observability utilities for monitoring, logging, and tracing.
"""

from backend.core.observability.context import create_log_context
from backend.core.observability.logging import (
    configure_logging,
    get_request_id,
    get_user_id,
    request_id_ctx_var,
    user_id_ctx_var,
)
from backend.core.observability.metrics import (
    circuit_breaker_state,
    record_circuit_breaker_failure,
    record_circuit_breaker_state,
    record_circuit_breaker_success,
    setup_metrics,
    update_db_pool_metrics,
)
from backend.core.observability.timing import timed_block, timing_decorator
from backend.core.observability.tracing import (
    create_span,
    get_current_trace_context,
    instrument_redis_client,
    instrument_sqlalchemy_engine,
    record_exception,
    setup_tracing,
)


__all__ = [
    "circuit_breaker_state",
    # Logging configuration
    "configure_logging",
    # Context creation
    "create_log_context",
    "create_span",
    "get_current_trace_context",
    "get_request_id",
    "get_user_id",
    "instrument_redis_client",
    "instrument_sqlalchemy_engine",
    "record_circuit_breaker_failure",
    "record_circuit_breaker_state",
    "record_circuit_breaker_success",
    "record_exception",
    # Context variables and getters
    "request_id_ctx_var",
    # Metrics
    "setup_metrics",
    # Tracing
    "setup_tracing",
    "timed_block",
    # Timing utilities
    "timing_decorator",
    "update_db_pool_metrics",
    "user_id_ctx_var",
]
