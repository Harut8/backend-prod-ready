# mypy: disable-error-code="no-any-return"
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException
import structlog

from backend.core.exceptions.http_exceptions import (
    ConflictError,
    InternalServerError,
    NotFoundError,
    RequestError,
    ServiceUnavailableError,
    ValidationError,
)


logger = structlog.get_logger(__name__)


def server_error_exception(request: Request, exc: Exception) -> JSONResponse:
    """Handle 500 Internal Server Error exceptions."""
    logger.exception(
        "Internal server error",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        error_type=type(exc).__name__,
        error_message=str(exc),
        exc_info=exc,
    )
    return InternalServerError(message=str(exc)).to_response()


def not_found_exception(request: Request, exc: HTTPException | NotFoundError) -> JSONResponse:
    """Handle 404 Not Found exceptions."""
    logger.info(
        "Resource not found",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        error_detail=str(exc.detail) if hasattr(exc, "detail") else str(exc),
    )
    if isinstance(exc, NotFoundError):
        return exc.to_response()
    custom_exc = NotFoundError(message=str(exc.detail) if exc.detail else "Resource not found")
    return custom_exc.to_response()


def bad_request_exception(request: Request, exc: HTTPException | RequestError) -> JSONResponse:
    """Handle 400 Bad Request exceptions."""
    logger.warning(
        "Bad request",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        error_detail=str(exc.detail) if hasattr(exc, "detail") else str(exc),
        error_type=type(exc).__name__,
    )
    if isinstance(exc, RequestError):
        return exc.to_response()
    custom_exc = RequestError(message=str(exc.detail) if exc.detail else "Bad request")
    return custom_exc.to_response()


def _format_field_path(loc: tuple) -> str:
    """Format field location into a user-friendly path.

    Handles special cases like:
    - ('body',) -> 'request body'
    - ('body', 14) -> 'request body' (14 is char position for JSON errors)
    - ('body', 'user', 'email') -> 'user.email'
    - ('query', 'page') -> 'page'
    - ('path', 'user_id') -> 'user_id'
    """
    if not loc:
        return "request"

    # Filter out 'body', 'query', 'path' prefixes and numeric positions
    parts = []
    for part in loc:
        # Skip location type prefixes
        if part in ("body", "query", "path", "header", "cookie"):
            continue
        # Skip numeric positions (used for JSON parse error positions)
        if isinstance(part, int):
            continue
        parts.append(str(part))

    if not parts:
        return "request body"

    return ".".join(parts)


def translate_validation_error(error_type: str, error_msg: str, field_path: str, loc: tuple | None = None) -> str:  # noqa: PLR0911, C901
    """Translate technical validation error to user-friendly message.

    Args:
        error_type: The Pydantic error type (e.g., 'json_invalid', 'missing')
        error_msg: The original error message
        field_path: The dot-separated field path
        loc: The original location tuple for special handling
    """
    # Format field path for display
    display_field = _format_field_path(loc) if loc else field_path

    if error_type == "json_invalid":
        return "The request body contains invalid JSON. Please check for syntax errors like missing quotes, commas, or brackets."

    if error_type == "type_error":
        if "str" in error_msg and "int" in error_msg:
            return f"'{display_field}' must be a number, not text."
        if "int" in error_msg and "str" in error_msg:
            return f"'{display_field}' must be text, not a number."
        if "bool" in error_msg:
            return f"'{display_field}' must be true or false."
        if "none" in error_msg.lower():
            return f"'{display_field}' cannot be null."
        return f"'{display_field}' has an invalid data type."

    if error_type == "missing":
        return f"'{display_field}' is required."

    if error_type in ("string_too_short", "string_too_long", "value_error"):
        if "too_short" in error_msg or error_type == "string_too_short":
            return f"'{display_field}' is too short."
        if "too_long" in error_msg or error_type == "string_too_long":
            return f"'{display_field}' is too long."
        if "invalid" in error_msg:
            return f"'{display_field}' contains an invalid value."
        return f"'{display_field}' has an invalid value."

    if error_type == "enum":
        return f"'{display_field}' must be one of the allowed values."

    if error_type == "list_type":
        return f"'{display_field}' must be a list."

    if error_type == "dict_type":
        return f"'{display_field}' must be an object."

    if error_type == "string_type":
        return f"'{display_field}' must be a string."

    if error_type == "int_type":
        return f"'{display_field}' must be an integer."

    if error_type == "float_type":
        return f"'{display_field}' must be a number."

    if error_type == "bool_type":
        return f"'{display_field}' must be true or false."

    if error_type == "url_type" or error_type == "url_parsing":
        return f"'{display_field}' must be a valid URL."

    if error_type == "email_type" or "email" in error_type:
        return f"'{display_field}' must be a valid email address."

    if error_type == "uuid_type" or error_type == "uuid_parsing":
        return f"'{display_field}' must be a valid UUID."

    if error_type == "datetime_type" or error_type == "datetime_parsing":
        return f"'{display_field}' must be a valid date/time."

    if error_type == "date_type" or error_type == "date_parsing":
        return f"'{display_field}' must be a valid date."

    if "greater_than" in error_type:
        return f"'{display_field}' is too small."

    if "less_than" in error_type:
        return f"'{display_field}' is too large."

    # Fallback with cleaner message
    return f"'{display_field}' is invalid: {error_msg}"


def extract_validation_errors(
    exc: RequestValidationError | PydanticValidationError,
) -> list[dict[str, str]]:
    """Extract and translate validation errors from FastAPI or Pydantic exceptions.

    Args:
        exc: The validation exception containing error details.

    Returns:
        List of error dictionaries with field, message, and type keys.
    """
    error_details = []
    for error in exc.errors():
        loc = error["loc"]
        error_type = error["type"]
        error_msg = error["msg"]

        # Format field path for display (skip 'body', 'query', etc. and numeric positions)
        display_field = _format_field_path(loc)

        user_message = translate_validation_error(error_type, error_msg, display_field, loc)
        error_details.append({"field": display_field, "message": user_message, "type": error_type})
    return error_details


def validation_exception(
    request: Request, exc: RequestValidationError | PydanticValidationError | ValidationError
) -> JSONResponse:
    """Handle validation errors from FastAPI and Pydantic.

    Translates technical validation errors into user-friendly messages.
    """
    if isinstance(exc, ValidationError):
        return exc.to_response()

    error_details = extract_validation_errors(exc)
    error_types = {error["type"] for error in exc.errors()}

    # Generate a descriptive overall message based on error types
    if "json_invalid" in error_types:
        overall_message = "Invalid JSON in request body. Please check for syntax errors."
    elif "missing" in error_types:
        missing_fields = [e["field"] for e in error_details if e["type"] == "missing"]
        if len(missing_fields) == 1:
            overall_message = f"Required field '{missing_fields[0]}' is missing."
        else:
            overall_message = f"Required fields are missing: {', '.join(missing_fields)}"
    elif len(error_details) == 1:
        # Single error - use its message as the overall message
        overall_message = error_details[0]["message"]
    else:
        # Multiple errors - summarize
        fields = [e["field"] for e in error_details]
        overall_message = f"Validation failed for: {', '.join(fields)}"

    log_message = (
        "Request validation failed" if isinstance(exc, RequestValidationError) else "Pydantic validation failed"
    )
    logger.warning(
        log_message,
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        validation_errors=error_details,
        error_count=len(error_details),
        failed_fields=[e["field"] for e in error_details],
    )

    custom_exc = ValidationError(message=overall_message, errors=error_details)
    return custom_exc.to_response()


def conflict_exception(request: Request, exc: HTTPException | ConflictError) -> JSONResponse:
    """Handle 409 Conflict exceptions."""
    logger.warning(
        "Conflict error",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        error_detail=str(exc.detail) if hasattr(exc, "detail") else str(exc),
        error_type=type(exc).__name__,
    )
    if isinstance(exc, ConflictError):
        return exc.to_response()
    custom_exc = ConflictError(message=str(exc.detail) if exc.detail else "Conflict")
    return custom_exc.to_response()


def service_unavailable_exception(request: Request, exc: ServiceUnavailableError) -> JSONResponse:
    """Handle 503 Service Unavailable exceptions with Retry-After header.

    This handler is crucial for fail-fast behavior:
    - Returns 503 (not 400/500) so clients know service is temporarily down
    - Includes Retry-After header so clients can implement smart retry logic
    - Allows load balancers to route to healthy instances
    """
    retry_after = exc.meta.get("retry_after", 60) if exc.meta else 60

    logger.warning(
        "Service unavailable",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        error_message=exc.message,
        error_code=exc.code,
        retry_after=retry_after,
    )

    response = exc.to_response()
    response.headers["Retry-After"] = str(retry_after)
    return response
