from collections.abc import Callable, Generator
from contextlib import contextmanager
import functools
import inspect
import time
from typing import Any

from fastapi import Response
import structlog


logger = structlog.get_logger(__name__)


def timing_decorator(*, include_header: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to measure route execution time with high precision.

    include_header: if True, adds X-Process-Time header to response
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = await func(*args, **kwargs)
            duration = time.perf_counter() - start
            logger.info("Function executed", function=func.__name__, duration=f"{duration:.6f}")

            if include_header and isinstance(result, Response):
                result.headers["X-Process-Time"] = f"{duration:.6f}"
            return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            result = func(*args, **kwargs)
            duration = time.perf_counter() - start
            logger.info("Function executed", function=func.__name__, duration=f"{duration:.6f}")
            return result

        # Detect if the function is async or sync
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


@contextmanager
def timed_block(label: str) -> Generator[None, None, None]:
    """
    Context manager to measure execution time of a code block.

    Args:
        label: A descriptive label for the block being timed.

    Usage:
        with timed_block("fetch_user_data"):
            result = await fetch_user()
    """
    start_time = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start_time
        logger.info("Block executed", label=label, duration_seconds=round(duration, 6))
