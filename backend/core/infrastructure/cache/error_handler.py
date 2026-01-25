from backend.core.exceptions.error_handler import ServiceErrorHandler
from backend.core.exceptions.http_exceptions import InternalServerError


# Cache service error handler
cache_service_error_handler = ServiceErrorHandler(
    default_exception=InternalServerError,
    preserve_exceptions=[
        ValueError,
        TypeError,
        KeyError,
    ],
)
