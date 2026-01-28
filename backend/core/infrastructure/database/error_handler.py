from collections.abc import Awaitable, Callable
import functools
import inspect
import socket
from typing import Any, NoReturn, ParamSpec, TypeVar

from asyncpg.exceptions import (
    ConnectionDoesNotExistError,
    ConnectionFailureError,
    InvalidAuthorizationSpecificationError,
    InvalidPasswordError,
    PostgresConnectionError,
)
from fastapi import HTTPException
from purgatory import CircuitBreakerFailed
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, TimeoutError as SQLAlchemyTimeoutError
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.exceptions.db_exceptions import (
    DatabaseCircuitBreakerOpenError,
    DatabaseDBAPIError,
    DatabaseDisconnectionError,
    DatabaseError,
    DatabaseInterfaceError,
    DatabaseOperationalError,
    DatabaseTimeoutError,
)
from backend.core.exceptions.http_exceptions import RequestTimeoutError


P = ParamSpec("P")
R = TypeVar("R")

logger = structlog.get_logger(__name__)

# Whether to include query params in error logs (only in DEBUG mode for security)
_INCLUDE_QUERY_PARAMS = SETTINGS.APP.LOG_LEVEL == "DEBUG"

QUERY_STATEMENT_MAX_LENGTH = 500
QUERY_PARAMS_MAX_LENGTH = 200

# Sensitive field names that should be redacted from logs
SENSITIVE_FIELD_PATTERNS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "api-key",
        "access_token",
        "refresh_token",
        "auth",
        "authorization",
        "credential",
        "private_key",
        "privatekey",
        "credit_card",
        "creditcard",
        "card_number",
        "cvv",
        "ssn",
        "social_security",
    }
)

REDACTED_VALUE = "[REDACTED]"


def _sanitize_params(params: dict[str, Any] | tuple[Any, ...] | list[Any] | None) -> str:
    """Sanitize query parameters by redacting sensitive values.

    Args:
        params: Query parameters (dict, tuple, or list)

    Returns:
        Sanitized string representation of parameters
    """
    if params is None:
        return ""

    if isinstance(params, dict):
        sanitized: dict[str, Any] = {}
        for key, value in params.items():
            key_lower = str(key).lower()
            # Check if key matches any sensitive pattern
            if any(pattern in key_lower for pattern in SENSITIVE_FIELD_PATTERNS):
                sanitized[key] = REDACTED_VALUE
            else:
                sanitized[key] = value
        return str(sanitized)

    # For positional parameters, we can't reliably detect sensitive data
    # by key name, so we redact values that look like secrets
    sanitized_list: list[Any] = []
    for value in params:
        str_value = str(value) if value is not None else ""
        # Redact long alphanumeric strings that might be tokens/secrets
        if len(str_value) > 32 and str_value.isalnum():
            sanitized_list.append(REDACTED_VALUE)
        else:
            sanitized_list.append(value)
    return str(tuple(sanitized_list) if isinstance(params, tuple) else sanitized_list)


def _extract_query_info(exception: Exception) -> dict[str, str]:
    """Extract query information from SQLAlchemy exceptions for debugging.

    Security note: Query parameters are only included in DEBUG mode to prevent
    accidental exposure of sensitive data in production logs. In production,
    only the query statement (truncated) and database error message are logged.
    """
    _info: dict[str, str] = {}

    if hasattr(exception, "statement") and exception.statement:
        _statement = str(exception.statement)
        _info["query_statement"] = (
            _statement[:QUERY_STATEMENT_MAX_LENGTH] + "..."
            if len(_statement) > QUERY_STATEMENT_MAX_LENGTH
            else _statement
        )

    # Only include query params in DEBUG mode to prevent sensitive data exposure
    if _INCLUDE_QUERY_PARAMS and hasattr(exception, "params") and exception.params:
        try:
            # Sanitize sensitive data before logging
            _params_str = _sanitize_params(exception.params)
            _info["query_params"] = (
                _params_str[:QUERY_PARAMS_MAX_LENGTH] + "..."
                if len(_params_str) > QUERY_PARAMS_MAX_LENGTH
                else _params_str
            )
        except Exception:  # noqa: BLE001
            _info["query_params"] = "[Unable to serialize params]"
    elif hasattr(exception, "params") and exception.params:
        # In non-DEBUG mode, indicate params exist but are redacted
        _info["query_params"] = "[REDACTED - set LOG_LEVEL=DEBUG to view]"

    if hasattr(exception, "orig") and exception.orig:
        _info["database_error"] = str(exception.orig)

    return _info


# Exception mapping configuration: (exception_types, log_message, target_exception, include_query_info)
# The order matters: more specific exceptions should come first
_EXCEPTION_HANDLERS: list[tuple[type | tuple[type, ...], str, type[Exception] | None, bool]] = [
    # Pass-through exceptions (re-raise as-is)
    (RequestTimeoutError, "Timeout exception", RequestTimeoutError, False),
    # Connection-related exceptions
    (
        (PostgresConnectionError, ConnectionFailureError, ConnectionDoesNotExistError, OSError, socket.gaierror),
        "Database disconnection error",
        DatabaseDisconnectionError,
        False,
    ),
    # Query-related exceptions (include query info in logs)
    (SQLAlchemyTimeoutError, "Database timeout error", DatabaseTimeoutError, True),
    (OperationalError, "Database operational error", DatabaseOperationalError, True),
    (InterfaceError, "Database interface error", DatabaseInterfaceError, False),
    (DBAPIError, "Database DBAPI error", DatabaseDBAPIError, True),
    # Circuit breaker exception
    (CircuitBreakerFailed, "Circuit breaker open", DatabaseCircuitBreakerOpenError, False),
]


def _handle_exception(e: Exception, func_name: str) -> NoReturn:
    """Centralized exception handling logic using strategy pattern.

    Maps low-level database exceptions to application-specific exceptions
    with appropriate logging.
    """
    # Handle HTTPException separately (re-raise without wrapping)
    if isinstance(e, HTTPException):
        logger.exception("HTTP exception", function=func_name)
        raise e

    # Check against registered exception handlers
    for exc_types, log_message, target_exc, include_query_info in _EXCEPTION_HANDLERS:
        if isinstance(e, exc_types):
            log_kwargs: dict[str, str] = {"function": func_name, "error_type": type(e).__name__}
            if include_query_info:
                log_kwargs.update(_extract_query_info(e))
            logger.exception(log_message, **log_kwargs)

            if target_exc is not None:
                raise target_exc from e
            raise e  # Re-raise original exception

    # Default fallback for unhandled exceptions
    logger.exception("Database error", function=func_name, error_type=type(e).__name__)
    raise DatabaseError from e


def database_error_handler(func: Callable[P, R]) -> Callable[P, R]:
    """
    Decorator that catches database exceptions and converts to custom exceptions.

    Preserves TimeoutException and HTTPException without conversion.
    Maps SQLAlchemy exceptions to custom database exceptions.
    """
    if inspect.iscoroutinefunction(func):
        return _wrap_async_function(func)  # type: ignore[return-value]
    return _wrap_sync_function(func)


def _wrap_async_function(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap an async function with database error handling."""

    @functools.wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            _handle_exception(e, func.__name__)

    return async_wrapper


def _wrap_sync_function(func: Callable[P, R]) -> Callable[P, R]:
    """Wrap a sync function with database error handling."""

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            _handle_exception(e, func.__name__)

    return sync_wrapper


# =============================================================================
# Startup Retry Helpers
# =============================================================================


def is_dns_resolution_error(exception: BaseException) -> bool:
    """Check if exception is a DNS resolution error (non-retryable)."""
    if isinstance(exception, socket.gaierror):
        # gaierror errno 8 = EAI_NONAME (hostname not found)
        return exception.args[0] == 8  # type: ignore [no-any-return]
    return False


def is_auth_error(exception: BaseException) -> bool:
    """Check if exception is an authentication error (non-retryable)."""
    return isinstance(exception, (InvalidPasswordError, InvalidAuthorizationSpecificationError))
