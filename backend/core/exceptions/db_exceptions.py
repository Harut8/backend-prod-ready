from backend.core.exceptions.error_codes import ErrorCode
from backend.core.exceptions.http_exceptions import (
    InternalServerError,
    RequestTimeoutError,
    ServiceUnavailableError,
)


class DatabaseError(InternalServerError):
    """Generic database error (HTTP 500).

    Base exception for database-related errors that don't fit specific categories.
    """

    message = "Database error"
    code = ErrorCode.DATABASE_ERROR


class DatabaseConnectionError(InternalServerError):
    """Database connection failed (HTTP 500).

    Raised when unable to establish initial connection to the database.
    """

    message = "Database connection error"
    code = ErrorCode.DATABASE_CONNECTION_ERROR


class DatabaseTimeoutError(RequestTimeoutError):
    """Database query timeout (HTTP 408).

    Raised when a database query exceeds the configured timeout limit.
    """

    message = "Database timeout error"
    code = ErrorCode.DATABASE_TIMEOUT_ERROR


class DatabaseDisconnectionError(ServiceUnavailableError):
    """Database connection lost (HTTP 503).

    Raised when an established database connection is unexpectedly closed.
    """

    message = "Database disconnection error"
    code = ErrorCode.DATABASE_DISCONNECTION_ERROR


class DatabaseInterfaceError(InternalServerError):
    """Database interface error (HTTP 500).

    Raised for errors related to the database interface itself (not the database).
    """

    message = "Database interface error"
    code = ErrorCode.DATABASE_INTERFACE_ERROR


class DatabaseOperationalError(InternalServerError):
    """Database operational error (HTTP 500).

    Raised for errors related to database operation (e.g., unexpected disconnect).
    """

    message = "Database operational error"
    code = ErrorCode.DATABASE_OPERATIONAL_ERROR


class DatabaseDBAPIError(InternalServerError):
    """Database DBAPI error (HTTP 500).

    Raised for errors in the database API layer.
    """

    message = "Database DBAPI error"
    code = ErrorCode.DATABASE_DBAPI_ERROR


class DatabaseCircuitBreakerOpenError(ServiceUnavailableError):
    """Database circuit breaker triggered (HTTP 503).

    Raised when the circuit breaker is open due to too many consecutive failures.
    The circuit breaker prevents cascading failures by temporarily blocking requests.

    Note: Uses 503 (Service Unavailable) rather than 429 (Rate Limit) because this
    indicates server-side unavailability, not client-side request throttling.
    """

    message = "Database circuit breaker is open - too many failures"
    code = ErrorCode.DATABASE_CIRCUIT_BREAKER_OPEN
