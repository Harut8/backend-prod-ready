from collections.abc import Callable
import functools
import inspect
from typing import Any, NoReturn

import structlog

from backend.core.utils.ids import safe_get_id_str


logger = structlog.get_logger(__name__)

# Fields that should never be logged
SENSITIVE_FIELDS = frozenset({
    "password",
    "token",
    "secret",
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "credential",
    "private_key",
})


class ServiceErrorHandler:
    def __init__(
        self,
        *,
        default_exception: type[Exception],
        exception_map: dict[type[Exception], type[Exception]] | None = None,
        preserve_exceptions: list[type[Exception]] | None = None,
        log_level: str = "exception",
        context_extractors: dict[str, Callable[[Any], Any]] | None = None,
    ) -> None:
        self._default_exception = default_exception
        self._exception_map = exception_map or {}
        self._preserve_exceptions = preserve_exceptions or []
        self._log_level = log_level
        self._context_extractors = context_extractors or {}
        self._context_extractors["user_id"] = lambda obj: safe_get_id_str(obj)

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):
            return self._wrap_async_function(func)
        return self._wrap_sync_function(func)

    def _wrap_async_function(self, func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                return self._handle_exception(e, func, args, kwargs)

        return async_wrapper

    def _wrap_sync_function(self, func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                return self._handle_exception(e, func, args, kwargs)

        return sync_wrapper

    def _handle_exception(
        self, exception: Exception, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> NoReturn:
        # Check if this exception should be preserved (re-raised as-is)
        for preserve_type in self._preserve_exceptions:
            if isinstance(exception, preserve_type):
                raise exception

        # Extract context for structured logging (excluding sensitive fields)
        context = self._extract_context(args, kwargs)
        context.update({
            "function": func.__name__,
            "error": str(exception),
            "error_type": type(exception).__name__,
        })

        # Log the error with appropriate level including full traceback for debugging
        if self._log_level == "exception":
            logger.exception("Service method failed", **context, exc_info=exception)
        else:
            log_method = getattr(logger, self._log_level)
            log_method("Service method failed", **context)

        # Map exception to appropriate domain exception
        target_exception = self._get_target_exception(exception)
        raise target_exception from exception

    def _extract_context(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {}

        # Try to extract context using configured extractors
        for field_name, extractor in self._context_extractors.items():
            # Skip sensitive fields
            if field_name.lower() in SENSITIVE_FIELDS:
                continue

            try:
                if args:
                    # For instance methods, args[0] is usually 'self', args[1] is first param
                    context[field_name] = extractor(args[1] if len(args) > 1 else args[0])
                elif kwargs:
                    # Try to extract from kwargs if available (skip sensitive keys)
                    for key, value in kwargs.items():
                        if key.lower() in SENSITIVE_FIELDS:
                            continue
                        extracted = extractor(value)
                        if extracted:
                            context[field_name] = extracted
                            break
            except (IndexError, AttributeError, TypeError):
                continue

        return context

    def _get_target_exception(self, exception: Exception) -> Exception:
        """Map source exception to target exception, preserving the error message.

        Args:
            exception: The original exception that was raised

        Returns:
            An instance of the target exception with the original error message
        """
        error_message = str(exception)

        # Check specific exception mappings first
        for source_type, target_type in self._exception_map.items():
            if isinstance(exception, source_type):
                try:
                    return target_type(message=error_message)  # type: ignore[call-arg]
                except TypeError:
                    return target_type()

        # Return default exception with original message if no mapping found
        try:
            return self._default_exception(message=error_message)  # type: ignore[call-arg]
        except TypeError:
            return self._default_exception()
