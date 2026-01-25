from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi_msgspec.responses import MsgSpecJSONResponse
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


def server_error_exception(_request: Request, exc: Exception) -> MsgSpecJSONResponse:
    """Handle 500 Internal Server Error exceptions."""
    # Log 500 errors with full context for debugging
    logger.exception(
        "Internal server error",
        path=_request.url.path,
        method=_request.method,
        client_host=_request.client.host if _request.client else None,
        error_type=type(exc).__name__,
        error_message=str(exc),
        exc_info=exc,
    )
    return InternalServerError(message=str(exc)).to_response()


def not_found_exception(_request: Request, exc: HTTPException | NotFoundError) -> MsgSpecJSONResponse:
    """Handle 404 Not Found exceptions."""
    # Log 404 errors for tracking access patterns
    logger.info(
        "Resource not found",
        path=_request.url.path,
        method=_request.method,
        client_host=_request.client.host if _request.client else None,
        error_detail=str(exc.detail) if hasattr(exc, "detail") else str(exc),
    )
    if isinstance(exc, NotFoundError):
        return exc.to_response()
    _custom_exc = NotFoundError(message=str(exc.detail) if exc.detail else "Resource not found")
    return _custom_exc.to_response()


def bad_request_exception(_request: Request, exc: HTTPException | RequestError) -> MsgSpecJSONResponse:
    """Handle 400 Bad Request exceptions."""
    # Log 400 errors with request details for debugging client issues
    logger.warning(
        "Bad request",
        path=_request.url.path,
        method=_request.method,
        client_host=_request.client.host if _request.client else None,
        error_detail=str(exc.detail) if hasattr(exc, "detail") else str(exc),
        error_type=type(exc).__name__,
    )
    if isinstance(exc, RequestError):
        return exc.to_response()
    _custom_exc = RequestError(message=str(exc.detail) if exc.detail else "Bad request")
    return _custom_exc.to_response()


def _translate_validation_error(error_type: str, error_msg: str, field_path: str) -> str:  # noqa: PLR0911, C901
    """Translate technical validation error to user-friendly message."""
    # JSON decode errors
    if error_type == "json_invalid":
        return "Invalid JSON format. Please check your request data and try again."

    # Type errors
    if error_type == "type_error":
        if "str" in error_msg and "int" in error_msg:
            return f"Field '{field_path}' must be a number, not text."
        if "int" in error_msg and "str" in error_msg:
            return f"Field '{field_path}' must be text, not a number."
        if "bool" in error_msg:
            return f"Field '{field_path}' must be true or false."
        return f"Field '{field_path}' has an invalid data type."

    # Missing required fields
    if error_type == "missing":
        return f"Field '{field_path}' is required."

    # Value errors
    if error_type == "value_error":
        if "too_short" in error_msg:
            return f"Field '{field_path}' is too short."
        if "too_long" in error_msg:
            return f"Field '{field_path}' is too long."
        if "invalid" in error_msg:
            return f"Field '{field_path}' contains an invalid value."
        return f"Field '{field_path}' has an invalid value."

    # Enum errors
    if error_type == "enum":
        return f"Field '{field_path}' must be one of the allowed values."

    # List/array errors
    if error_type == "list_type":
        return f"Field '{field_path}' must be a list of items."

    # Default fallback
    return f"Field '{field_path}' has an error: {error_msg}"


def _extract_validation_errors(
    exc: RequestValidationError | PydanticValidationError,
) -> list[dict[str, str]]:
    """Extract and translate validation errors from FastAPI or Pydantic exceptions.

    Args:
        exc: The validation exception containing error details.

    Returns:
        List of error dictionaries with field, message, and type keys.
    """
    _error_details = []
    for error in exc.errors():
        _field_path = ".".join(str(loc) for loc in error["loc"])
        _error_type = error["type"]
        _error_msg = error["msg"]
        _user_message = _translate_validation_error(_error_type, _error_msg, _field_path)
        _error_details.append({"field": _field_path, "message": _user_message, "type": _error_type})
    return _error_details


def validation_exception(
    _request: Request, exc: RequestValidationError | PydanticValidationError | ValidationError
) -> MsgSpecJSONResponse:
    """Handle validation errors from FastAPI and Pydantic.

    Translates technical validation errors into user-friendly messages.
    """
    if isinstance(exc, ValidationError):
        return exc.to_response()

    # Extract and translate validation errors
    _error_details = _extract_validation_errors(exc)

    # Determine overall message based on error types
    if any(error["type"] == "json_invalid" for error in exc.errors()):
        _overall_message = "Invalid request format. Please check your JSON data."
    elif any(error["type"] == "missing" for error in exc.errors()):
        _overall_message = "Some required fields are missing."
    else:
        _overall_message = "Please check your input data and try again."

    # Log validation errors with detailed field information for debugging
    _log_message = (
        "Request validation failed" if isinstance(exc, RequestValidationError) else "Pydantic validation failed"
    )
    logger.warning(
        _log_message,
        path=_request.url.path,
        method=_request.method,
        client_host=_request.client.host if _request.client else None,
        validation_errors=_error_details,
        error_count=len(_error_details),
        failed_fields=[e["field"] for e in _error_details],
    )

    _custom_exc = ValidationError(message=_overall_message, errors=_error_details)
    return _custom_exc.to_response()


def conflict_exception(_request: Request, exc: HTTPException | ConflictError) -> MsgSpecJSONResponse:
    """Handle 409 Conflict exceptions."""
    # Log conflict errors for tracking duplicate/concurrent operations
    logger.warning(
        "Conflict error",
        path=_request.url.path,
        method=_request.method,
        client_host=_request.client.host if _request.client else None,
        error_detail=str(exc.detail) if hasattr(exc, "detail") else str(exc),
        error_type=type(exc).__name__,
    )
    if isinstance(exc, ConflictError):
        return exc.to_response()
    _custom_exc = ConflictError(message=str(exc.detail) if exc.detail else "Conflict")
    return _custom_exc.to_response()


def service_unavailable_exception(_request: Request, exc: ServiceUnavailableError) -> MsgSpecJSONResponse:
    """Handle 503 Service Unavailable exceptions with Retry-After header.

    This handler is crucial for fail-fast behavior:
    - Returns 503 (not 400/500) so clients know service is temporarily down
    - Includes Retry-After header so clients can implement smart retry logic
    - Allows load balancers to route to healthy instances
    """
    retry_after = exc.meta.get("retry_after", 60) if exc.meta else 60

    logger.warning(
        "Service unavailable",
        path=_request.url.path,
        method=_request.method,
        client_host=_request.client.host if _request.client else None,
        error_message=exc.message,
        error_code=exc.code,
        retry_after=retry_after,
    )

    response = exc.to_response()
    response.headers["Retry-After"] = str(retry_after)
    return response
