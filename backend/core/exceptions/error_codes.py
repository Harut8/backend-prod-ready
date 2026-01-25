from enum import Enum


class ErrorCode(str, Enum):
    """Enumeration of all error codes used in the application.

    Error codes are used by clients to programmatically handle specific errors.
    They should be stable across API versions to avoid breaking client code.
    """

    # Generic HTTP Error Codes
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT_ERROR = "CONFLICT_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    BAD_GATEWAY = "BAD_GATEWAY"

    # Authentication & Authorization
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # JWT Error Codes
    JWT_TOKEN_ERROR = "JWT_TOKEN_ERROR"  # noqa: S105
    JWT_INVALID_TOKEN = "JWT_INVALID_TOKEN"  # noqa: S105
    JWT_EXPIRED_TOKEN = "JWT_EXPIRED_TOKEN"  # noqa: S105
    JWT_MISSING_TOKEN = "JWT_MISSING_TOKEN"  # noqa: S105

    # Database Error Codes
    DATABASE_ERROR = "DATABASE_ERROR"
    DATABASE_CONNECTION_ERROR = "DATABASE_CONNECTION_ERROR"
    DATABASE_TIMEOUT_ERROR = "DATABASE_TIMEOUT_ERROR"
    DATABASE_DISCONNECTION_ERROR = "DATABASE_DISCONNECTION_ERROR"
    DATABASE_INTERFACE_ERROR = "DATABASE_INTERFACE_ERROR"
    DATABASE_OPERATIONAL_ERROR = "DATABASE_OPERATIONAL_ERROR"
    DATABASE_DBAPI_ERROR = "DATABASE_DBAPI_ERROR"
    DATABASE_CIRCUIT_BREAKER_OPEN = "DATABASE_CIRCUIT_BREAKER_OPEN"

    def __str__(self) -> str:
        """Return the string value of the error code."""
        return self.value
