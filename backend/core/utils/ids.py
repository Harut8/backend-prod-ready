from typing import Any
import uuid


def to_str_id(id_value: uuid.UUID | str | None) -> str:
    """Convert a UUID or string ID to string, or return empty string if None."""
    if id_value is None:
        return ""
    return str(id_value)


def safe_get_id_str(obj: Any, id_attr: str = "id") -> str:
    """Safely extract an ID as string from an object, UUID, or string."""
    if obj is None:
        return ""

    if isinstance(obj, (uuid.UUID, str)):
        return to_str_id(obj)

    _id_value = getattr(obj, id_attr, None)
    return to_str_id(_id_value)
