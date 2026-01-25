from collections.abc import Callable
import functools
import inspect
from typing import Any, NoReturn

import structlog

from backend.core.utils.ids import safe_get_id_str


logger = structlog.get_logger(__name__)


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
            except BaseException as e:
                # Re-raise system exceptions immediately - don't handle them
                if isinstance(e, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                # Handle all other exceptions (which are all Exception subclasses)
                # Safe to cast since we filtered out non-Exception BaseExceptions above
                return self._handle_exception(e, func, args, kwargs)  # type: ignore[arg-type]

        return async_wrapper

    def _wrap_sync_function(self, func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except BaseException as e:
                # Re-raise system exceptions immediately - don't handle them
                if isinstance(e, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                # Handle all other exceptions (which are all Exception subclasses)
                # Safe to cast since we filtered out non-Exception BaseExceptions above
                return self._handle_exception(e, func, args, kwargs)  # type: ignore[arg-type]

        return sync_wrapper

    def _handle_exception(
        self, exception: Exception, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> NoReturn:
        # Check if this exception should be preserved (re-raised as-is)
        for preserve_type in self._preserve_exceptions:
            if isinstance(exception, preserve_type):
                raise exception

        # Extract context for structured logging
        _context = self._extract_context(args, kwargs)
        _context.update({"function": func.__name__, "error": str(exception), "error_type": type(exception).__name__})

        # Log the error with appropriate level including full traceback for debugging
        if self._log_level == "exception":
            # Use logger.exception to include full stack trace for debugging production issues
            logger.exception("Service method failed", **_context, exc_info=exception)
        else:
            _log_method = getattr(logger, self._log_level)
            _log_method("Service method failed", **_context)

        # Map exception to appropriate domain exception
        _target_exception = self._get_target_exception(exception)
        raise _target_exception from exception

    def _extract_context(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        _context = {}

        # Try to extract context using configured extractors
        for field_name, extractor in self._context_extractors.items():
            try:
                if args:
                    # For instance methods, args[0] is usually 'self', args[1] is first param
                    _context[field_name] = extractor(args[1] if len(args) > 1 else args[0])
                elif kwargs:
                    # Try to extract from kwargs if available
                    for value in kwargs.values():
                        extracted = extractor(value)
                        if extracted:
                            _context[field_name] = extracted
                            break
            except (IndexError, AttributeError, TypeError):
                # Skip if extraction fails
                continue

        return _context

    def _get_target_exception(self, exception: Exception) -> Exception:
        """Map source exception to target exception, preserving the error message.

        Args:
            exception: The original exception that was raised

        Returns:
            An instance of the target exception with the original error message
        """
        _error_message = str(exception)

        # Check specific exception mappings first
        for source_type, target_type in self._exception_map.items():
            if isinstance(exception, source_type):
                # Try to pass the message to preserve context
                try:
                    return target_type(message=_error_message)  # type: ignore[call-arg]
                except TypeError:
                    # If the exception doesn't accept message parameter, create without it
                    return target_type()

        # Return default exception with original message if no mapping found
        try:
            return self._default_exception(message=_error_message)  # type: ignore[call-arg]
        except TypeError:
            # If the exception doesn't accept message parameter, create without it
            return self._default_exception()
