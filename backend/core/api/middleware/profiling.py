"""
Usage:
    # In main.py
    from backend.core.api.middleware.profiling import profiling_middleware
    app.middleware("http")(profiling_middleware)

    # On-demand profiling
    curl "http://localhost:8000/api/endpoint?profile=true"

    # With header authentication
    curl -H "X-Profile-Token: secret" "http://localhost:8000/api/endpoint?profile=true"

Environment Variables:
    PROFILING_ENABLED: Enable/disable profiling (default: false)
    PROFILE_SAMPLE_RATE: Random sampling rate 0.0-1.0 (default: 0.001 = 0.1%)
    REQUIRE_PROFILE_HEADER: Require X-Profile-Token header (default: false)
    PROFILE_HEADER_TOKEN: Secret token for header authentication
    PROFILED_ENDPOINTS: Comma-separated list of endpoints to profile (empty = all)
    PROFILE_RETENTION_DAYS: Days to keep profile files (default: 7)
    PROFILE_INTERVAL_MS: Sampling interval in milliseconds (default: 1.0)
"""

import asyncio
from collections.abc import Awaitable, Callable
import hmac
from pathlib import Path
import random
import time

from backend.core.conf.settings import SETTINGS
from backend.core.utils.datetime import get_current_utc_time
from fastapi import Request, Response
from pyinstrument import Profiler
from pyinstrument.renderers.speedscope import SpeedscopeRenderer
import structlog


logger = structlog.get_logger(__name__)

# Profile storage directory (created lazily when needed)
PROFILE_DIR = Path(__file__).parent.parent.parent.parent / "profiles"


def _get_current_timestamp() -> int:
    """Get current Unix timestamp (seconds since epoch)."""
    return int(get_current_utc_time().timestamp())


def _get_current_date_path() -> str:
    """Get current date path for profile organization (YYYY/MM/DD format)."""
    return str(get_current_utc_time().strftime("%Y/%m/%d"))


def _get_profile_path(endpoint: str) -> Path | None:
    """Get the profile directory for a specific endpoint and date.

    Returns None if the directory cannot be created (e.g., read-only filesystem).
    """
    # Create date-based subdirectory
    _date_str = _get_current_date_path()

    # Sanitize endpoint path for filesystem
    _endpoint_safe = endpoint.replace("/", "_").strip("_") or "root"

    _profile_path = PROFILE_DIR / "pyinstrument" / _date_str / _endpoint_safe
    try:
        _profile_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Cannot create profile directory", error=str(e), path=str(_profile_path))
        return None

    return _profile_path


def _cleanup_old_profiles() -> None:
    """Remove profile files older than retention period."""
    if not PROFILE_DIR.exists():
        return

    _cutoff_time = _get_current_timestamp() - (SETTINGS.PROFILING.PROFILE_RETENTION_DAYS * 86400)

    try:
        for _profile_file in PROFILE_DIR.rglob("*.json"):
            if _profile_file.stat().st_mtime < _cutoff_time:
                _profile_file.unlink()

        for _profile_file in PROFILE_DIR.rglob("*.html"):
            if _profile_file.stat().st_mtime < _cutoff_time:
                _profile_file.unlink()

        # Remove empty directories
        for _dir_path in sorted(PROFILE_DIR.rglob("*"), reverse=True):
            if _dir_path.is_dir() and not any(_dir_path.iterdir()):
                _dir_path.rmdir()

    except OSError as e:
        logger.warning("Failed to cleanup old profiles", error=str(e))


async def _save_profile_locally(
    profiler: Profiler,
    request: Request,
) -> tuple[Path | None, Path | None]:
    """
    Save profile data to local filesystem.

    Args:
        profiler: The Profiler instance with captured data
        request: The FastAPI request object

    Returns:
        Tuple of (json_path, html_path) or (None, None) on error
    """
    try:
        # Get profile directory
        _profile_path = _get_profile_path(request.url.path)
        if _profile_path is None:
            return None, None

        # Generate filename
        _timestamp = _get_current_timestamp()
        _request_id = request.headers.get("X-Request-ID", f"{_timestamp}")[:8]
        _base_filename = f"profile_{_request_id}_{_timestamp}"

        # Save Speedscope JSON format (for speedscope.app)
        _json_path = _profile_path / f"{_base_filename}.json"
        _speedscope_data = profiler.output(SpeedscopeRenderer())

        # Save HTML format (for browser viewing)
        _html_path = _profile_path / f"{_base_filename}.html"
        _html_data = profiler.output_html()

        # Run file I/O in thread pool to avoid blocking
        _loop = asyncio.get_running_loop()
        await _loop.run_in_executor(None, lambda: _json_path.write_text(_speedscope_data, encoding="utf-8"))
        await _loop.run_in_executor(None, lambda: _html_path.write_text(_html_data, encoding="utf-8"))

        # Periodically cleanup old profiles (async, don't wait)
        if random.random() < 0.01:  # 1% chance to trigger cleanup
            # Fire-and-forget background task for cleanup in thread pool
            asyncio.get_running_loop().run_in_executor(None, _cleanup_old_profiles)

    except OSError as e:
        logger.warning("Failed to save profile", error=str(e), path=request.url.path)
        return None, None
    else:
        return _json_path, _html_path


def _should_profile_request(request: Request) -> bool:
    """
    Determine if this request should be profiled.

    Decision logic:
    1. Profiling must be enabled
    2. If header required, validate token
    3. If query param present, profile
    4. If endpoint filter set, check endpoint
    5. Apply random sampling
    """
    if not SETTINGS.PROFILING.PROFILING_ENABLED:
        return False

    # Check header authorization if required (timing-safe comparison)
    if SETTINGS.PROFILING.REQUIRE_PROFILE_HEADER:
        _header_token = request.headers.get("X-Profile-Token") or ""
        _expected_token = SETTINGS.PROFILING.PROFILE_HEADER_TOKEN.get_secret_value()
        if not hmac.compare_digest(_header_token, _expected_token):
            return False

    # Explicit query param always enables profiling
    if request.query_params.get("profile") == "true":
        return True

    # Check endpoint filter and apply random sampling
    if SETTINGS.PROFILING.PROFILED_ENDPOINTS and request.url.path not in SETTINGS.PROFILING.PROFILED_ENDPOINTS:
        return False

    # Apply random sampling
    return bool(random.random() < SETTINGS.PROFILING.PROFILE_SAMPLE_RATE)


async def profiling_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """
    FastAPI middleware for conditional profiling.

    This middleware wraps request handling with Pyinstrument profiling
    when certain conditions are met. It supports:
    """
    if not _should_profile_request(request):
        return await call_next(request)

    # Start profiling
    _profiler = Profiler(
        interval=SETTINGS.PROFILING.PROFILE_INTERVAL_MS / 1000,  # Convert ms to seconds
        async_mode="enabled",  # Critical for async/await support
    )
    _profiler.start()

    _start_time = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception as e:
        # Ensure we stop profiler and save profile even on errors
        _profiler.stop()
        _duration_ms = (time.perf_counter() - _start_time) * 1000

        # Save profile for failed requests (useful for debugging)
        _json_path, _html_path = await _save_profile_locally(_profiler, request)

        if _json_path:
            logger.info(
                "Profile saved for failed request",
                path=request.url.path,
                error=type(e).__name__,
                duration_ms=f"{_duration_ms:.2f}",
                json=str(_json_path),
                html=str(_html_path),
            )

        raise  # Re-raise the exception

    _profiler.stop()
    _duration_ms = (time.perf_counter() - _start_time) * 1000

    # Save profile asynchronously
    _json_path, _html_path = await _save_profile_locally(_profiler, request)

    if _json_path:
        # Add headers indicating profiling occurred and where to find results
        response.headers["X-Profiled"] = "true"
        response.headers["X-Profile-Duration-Ms"] = f"{_duration_ms:.2f}"
        # Only expose internal path in non-production environments
        if _html_path is not None and SETTINGS.APP.ENVIRONMENT != "prod":
            response.headers["X-Profile-Path"] = str(_html_path.relative_to(PROFILE_DIR.parent))

        # Log profile location
        logger.info(
            "Profile saved",
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=f"{_duration_ms:.2f}",
            json=str(_json_path),
            html=str(_html_path),
        )

    return response
