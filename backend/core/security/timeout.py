import asyncio
from collections.abc import Awaitable, Callable
import functools
import inspect
from typing import ParamSpec, TypeVar

import structlog

from backend.core.exceptions.http_exceptions import RequestTimeoutError


P = ParamSpec("P")
R = TypeVar("R")


logger = structlog.get_logger(__name__)


def timeout(
    seconds: float, *, set_timeout_occurred: bool = False
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Decorator that enforces a timeout on async function execution.

    Uses asyncio.wait_for() which properly cancels the operation on timeout,
    ensuring resources are released immediately. This is critical for database
    operations to prevent connection pool exhaustion.
    """
    if seconds <= 0:
        msg = f"Timeout must be positive, got {seconds}"
        raise ValueError(msg)

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if not inspect.iscoroutinefunction(func):
            msg = (
                f"@timeout decorator only supports async functions. "
                f"Function '{func.__qualname__}' is not async. "
                f"For sync DB operations, consider converting to async or using DB-level timeouts."
            )
            raise TypeError(msg)
        return _wrap_async_function(func, seconds, set_timeout_occurred=set_timeout_occurred)

    return decorator


def timeout_uow_aware(seconds: float) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Decorator that enforces a timeout on async function execution with UOW awareness.

    This is a convenience wrapper around @timeout(seconds, set_timeout_occurred=True).
    When decorating a method of a BaseUnitOfWork instance, it sets the _timeout_occurred
    flag when a timeout happens. This enables proper cleanup delays for asyncpg
    connection handling.
    """
    return timeout(seconds, set_timeout_occurred=True)


def _wrap_async_function(
    func: Callable[P, Awaitable[R]], timeout_seconds: float, *, set_timeout_occurred: bool = False
) -> Callable[P, Awaitable[R]]:
    """Wrap an async function with timeout enforcement using asyncio.wait_for().

    This properly cancels the async operation on timeout, ensuring resources
    like DB connections are released immediately.

    """

    @functools.wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        func_name = f"{func.__module__}.{func.__qualname__}"
        logger.debug("Applying timeout to async function", function=func_name, timeout=timeout_seconds)

        try:
            return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
        except TimeoutError as err:
            # Set timeout flag on instance if requested (for UOW cleanup delays)
            if set_timeout_occurred and args and hasattr(args[0], "_timeout_occurred"):
                args[0]._timeout_occurred = True  # noqa: SLF001
                logger.info(
                    "Set timeout flag on instance",
                    function=func_name,
                    instance_class=args[0].__class__.__name__,
                )

            # Log detailed information for debugging (internal only)
            logger.warning(
                "Function execution timed out",
                function=func_name,
                timeout_seconds=timeout_seconds,
            )
            # Return generic message to clients to avoid leaking internal function names
            raise RequestTimeoutError(
                message="Request timed out. Please try again.",
                meta={
                    "retry_after": 5,
                    "suggestion": "System is under load. Please retry with exponential backoff.",
                },
            ) from err

    return async_wrapper
