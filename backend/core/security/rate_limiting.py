from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
import structlog

from backend.core.conf.settings import SETTINGS
from backend.core.security.proxy_validation import extract_client_ip_secure


logger = structlog.get_logger(__name__)


def extract_client_ip(request: Request) -> str:
    """
    Securely extract the real client IP address from FastAPI request.

    SECURITY: Only trusts X-Forwarded-For headers from configured trusted proxies.
    This prevents rate limit bypass via header spoofing attacks.

    Handles:
    - Reverse proxy headers (x-forwarded-for) - only from trusted proxies
    - Comma-separated proxy chains (takes first IP)
    - Fallback to request.client.host for untrusted sources
    """
    x_forwarded_for = request.headers.get("x-forwarded-for")
    direct_ip = request.client.host if request.client else None

    client_ip: str = extract_client_ip_secure(x_forwarded_for, direct_ip)
    logger.debug("Extracted client IP", ip=client_ip, direct_ip=direct_ip)
    return client_ip


def get_user_id_or_ip(request: Request) -> str:
    """
    Get user ID from JWT token or fall back to IP address for rate limiting.

    This allows authenticated users to have rate limits tied to their account
    while unauthenticated users are rate-limited by IP address.
    """
    # Try to get user ID from token
    _auth_header = request.headers.get("Authorization")
    if _auth_header and _auth_header.startswith("Bearer "):
        try:
            # Lazy import to avoid circular dependency
            from backend.core.auth.jwt import jwt_decode  # noqa: PLC0415
            from backend.core.exceptions.jwt_exceptions import JWTTokenError  # noqa: PLC0415

            _token = _auth_header.split(" ")[1]
            _payload = jwt_decode(_token)
            if _payload and "user_id" in _payload:
                return f"user:{_payload['user_id']}"
        except JWTTokenError as e:
            # Handle all JWT-related exceptions (expired, invalid, malformed, etc.)
            logger.debug("Failed to decode token for rate limiting", error=str(e))
            # Fall through to IP-based rate limiting
        except (IndexError, KeyError) as e:
            # Handle malformed Authorization header or missing user_id
            logger.debug("Malformed auth header for rate limiting", error=str(e))
            # Fall through to IP-based rate limiting

    # Fall back to IP address
    return f"ip:{extract_client_ip(request)}"


# Disable rate limiting if RATE_LIMIT_ENABLED=false (for load testing)
# Enable in-memory fallback to maintain rate limiting during Redis outages
# This prevents either complete availability loss (fail closed) or security bypass (fail open)
limiter = Limiter(
    key_func=get_user_id_or_ip,
    storage_uri=SETTINGS.REDIS.REDIS_URL,
    default_limits=[f"{SETTINGS.RATE_LIMIT.RATE_LIMIT_REQUESTS_PER_MINUTE}/minute"],
    enabled=SETTINGS.RATE_LIMIT.RATE_LIMIT_ENABLED,
    in_memory_fallback_enabled=True,
)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded with Retry-After header.

    Includes Retry-After header to help clients implement proper backoff
    and prevent thundering herd when the rate limit resets.
    """
    _client_id = get_user_id_or_ip(request)

    # Extract retry-after from the exception detail (format: "X per Y minute")
    # Default to 60 seconds if we can't parse it
    _retry_after = 60
    if exc.detail:
        try:
            # slowapi provides the limit in format "X per Y minute/second/hour"
            _parts = str(exc.detail).split()
            if "minute" in _parts:
                _retry_after = 60
            elif "second" in _parts:
                _retry_after = 1
            elif "hour" in _parts:
                _retry_after = 3600
        except (ValueError, IndexError):
            pass

    logger.warning(
        "Rate limit exceeded",
        path=request.url.path,
        method=request.method,
        client=_client_id,
        limit=exc.detail,
        retry_after=_retry_after,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )

    _response = JSONResponse(
        status_code=429,
        content={"detail": {"message": "Rate limit exceeded. Please try again later.", "code": "RATE_LIMIT_EXCEEDED"}},
    )
    _response.headers["Retry-After"] = str(_retry_after)
    return _response
