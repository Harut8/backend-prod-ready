# Database Exceptions
from backend.core.exceptions.db_exceptions import (
    DatabaseCircuitBreakerOpenError,
    DatabaseConnectionError,
    DatabaseDBAPIError,
    DatabaseDisconnectionError,
    DatabaseError,
    DatabaseInterfaceError,
    DatabaseOperationalError,
    DatabaseTimeoutError,
)

# Error Codes
from backend.core.exceptions.error_codes import ErrorCode

# Error Formatter
from backend.core.exceptions.error_formatter import KinoneeErrorFormatter

# HTTP Exceptions
from backend.core.exceptions.http_exceptions import (
    AuthenticationFailedError,
    BadGatewayError,
    ConflictError,
    InternalServerError,
    MethodNotAllowedError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitExceededError,
    RequestError,
    RequestTimeoutError,
    ServiceException,
    ServiceUnavailableError,
    ValidationError,
)

# JWT Exceptions
from backend.core.exceptions.jwt_exceptions import (
    JWTExpiredSignatureError,
    JWTInvalidTokenError,
    JWTMissingTokenError,
    JWTTokenError,
)


__all__ = [
    "AuthenticationFailedError",
    "BadGatewayError",
    "ConflictError",
    "DatabaseCircuitBreakerOpenError",
    "DatabaseConnectionError",
    "DatabaseDBAPIError",
    "DatabaseDisconnectionError",
    # Database Errors
    "DatabaseError",
    "DatabaseInterfaceError",
    "DatabaseOperationalError",
    "DatabaseTimeoutError",
    # Error Codes
    "ErrorCode",
    # Error Formatter
    "KinoneeErrorFormatter",
    # HTTP 5xx Errors
    "InternalServerError",
    "JWTExpiredSignatureError",
    "JWTInvalidTokenError",
    "JWTMissingTokenError",
    # JWT Errors
    "JWTTokenError",
    "MethodNotAllowedError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitExceededError",
    # HTTP 4xx Errors
    "RequestError",
    "RequestTimeoutError",
    # Base
    "ServiceException",
    "ServiceUnavailableError",
    "ValidationError",
]
