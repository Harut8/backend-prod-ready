from backend.core.exceptions.error_codes import ErrorCode
from backend.core.exceptions.http_exceptions import AuthenticationFailedError


class JWTTokenError(AuthenticationFailedError):
    """Base JWT token error (HTTP 401).

    Generic exception for JWT token validation failures.
    """

    message = "Invalid JWT Token."
    code = ErrorCode.JWT_TOKEN_ERROR


class JWTInvalidTokenError(JWTTokenError):
    """JWT token is malformed or invalid (HTTP 401).

    Raised when the token structure is invalid, signature verification fails,
    or the token contains invalid claims.
    """

    message = "Invalid JWT Token."
    code = ErrorCode.JWT_INVALID_TOKEN


class JWTExpiredSignatureError(JWTTokenError):
    """JWT token has expired (HTTP 401).

    Raised when the token's expiration time (exp claim) has passed.
    Client should refresh the token or re-authenticate.
    """

    message = "Expired JWT Signature."
    code = ErrorCode.JWT_EXPIRED_TOKEN


class JWTMissingTokenError(JWTTokenError):
    """JWT token is missing from request (HTTP 401).

    Raised when an endpoint requires authentication but no token was provided
    in the Authorization header or cookies.
    """

    message = "JWT Token is missing."
    code = ErrorCode.JWT_MISSING_TOKEN
