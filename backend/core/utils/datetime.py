from datetime import UTC, date, datetime, timedelta
from typing import Any


def date_plus_days(dt: date, days: int) -> date:
    """Add days to a date."""
    return dt + timedelta(days=days)


def get_current_utc_time() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(tz=UTC)


def get_current_utc_time_plus_minutes(minutes: int) -> datetime:
    """Get current UTC datetime plus specified minutes."""
    return get_current_utc_time() + timedelta(minutes=minutes)


def get_current_utc_time_plus_days(days: int) -> datetime:
    """Get current UTC datetime plus specified days."""
    return get_current_utc_time() + timedelta(days=days)


def timestamp_to_utc_datetime(timestamp: int | float) -> datetime:
    """Convert Unix timestamp to UTC datetime."""
    return datetime.fromtimestamp(timestamp, tz=UTC)


def get_current_utc_date() -> date:
    """Get current UTC date."""
    return datetime.now(tz=UTC).date()


def get_current_utc_date_plus_days(days: int) -> date:
    """Get current UTC date plus specified days."""
    return get_current_utc_date() + timedelta(days=days)


def get_current_utc_date_and_month() -> tuple[date, str]:
    """Get current UTC date and month string (YYYY-MM format)."""
    today = get_current_utc_date()
    month_str = today.strftime("%Y-%m")
    return today, month_str


def parse_datetime_from_cache(value: Any) -> datetime | None:
    """
    Parse datetime from cache value.

    Cache service may return datetime fields as either strings (from JSON)
    or already-parsed datetime objects. This function handles both cases.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    _msg = f"Expected str, datetime, or None, got {type(value).__name__}"
    raise TypeError(_msg)
