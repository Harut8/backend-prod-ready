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


def translate_validation_error(error_type: str, error_msg: str, field_path: str) -> str:  # noqa: PLR0911, C901
    """Translate technical validation error to user-friendly message."""
    if error_type == "json_invalid":
        return "Invalid JSON format. Please check your request data and try again."

    if error_type == "type_error":
        if "str" in error_msg and "int" in error_msg:
            return f"Field '{field_path}' must be a number, not text."
        if "int" in error_msg and "str" in error_msg:
            return f"Field '{field_path}' must be text, not a number."
        if "bool" in error_msg:
            return f"Field '{field_path}' must be true or false."
        return f"Field '{field_path}' has an invalid data type."

    if error_type == "missing":
        return f"Field '{field_path}' is required."

    if error_type == "value_error":
        if "too_short" in error_msg:
            return f"Field '{field_path}' is too short."
        if "too_long" in error_msg:
            return f"Field '{field_path}' is too long."
        if "invalid" in error_msg:
            return f"Field '{field_path}' contains an invalid value."
        return f"Field '{field_path}' has an invalid value."

    if error_type == "enum":
        return f"Field '{field_path}' must be one of the allowed values."

    if error_type == "list_type":
        return f"Field '{field_path}' must be a list of items."

    return f"Field '{field_path}' has an error: {error_msg}"


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
        field_path = ".".join(str(loc) for loc in error["loc"])
        error_type = error["type"]
        error_msg = error["msg"]
        user_message = translate_validation_error(error_type, error_msg, field_path)
        error_details.append({"field": field_path, "message": user_message, "type": error_type})
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

    if any(error["type"] == "json_invalid" for error in exc.errors()):
        overall_message = "Invalid request format. Please check your JSON data."
    elif any(error["type"] == "missing" for error in exc.errors()):
        overall_message = "Some required fields are missing."
    else:
        overall_message = "Please check your input data and try again."

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
