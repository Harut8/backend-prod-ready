from typing import Any, TypeVar


T = TypeVar("T")


def ensure_list(value: list[T] | None) -> list[T]:
    """Return the list or an empty list if None."""
    return value or []


def ensure_set(value: set[T] | None) -> set[T]:
    """Return the set or an empty set if None."""
    return value or set()


def safe_get_list(obj: Any, attr_name: str) -> list[Any]:
    """Safely get a list attribute from an object or dict."""
    if isinstance(obj, dict):
        return ensure_list(obj.get(attr_name, None))
    return ensure_list(getattr(obj, attr_name, None))
