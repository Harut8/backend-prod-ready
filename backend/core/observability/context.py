"""
Logging context utilities for creating structured log context dictionaries.
"""

from typing import Any

from backend.core.utils.ids import safe_get_id_str


def create_log_context(user: Any, **additional_fields: Any) -> dict[str, Any]:
    """
    Create a logging context dictionary with user ID and additional fields.

    This is useful for manually binding context to structlog loggers.

    Args:
        user: A user object (with .id attribute), UUID, or string ID.
        **additional_fields: Additional key-value pairs to include in the context.

    Returns:
        A dictionary suitable for use with structlog.bind().

    Usage:
        logger = structlog.get_logger()
        ctx = create_log_context(user, action="login", ip=request.client.host)
        logger.bind(**ctx).info("User action")
    """
    context: dict[str, Any] = {"user_id": safe_get_id_str(user)}
    context.update(additional_fields)
    return context
