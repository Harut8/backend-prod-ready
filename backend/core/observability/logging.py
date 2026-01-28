from contextvars import ContextVar
import logging
import logging.config
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

import structlog
from structlog.stdlib import LoggerFactory
from structlog.types import EventDict, WrappedLogger
import yaml  # type: ignore [import-untyped]


if TYPE_CHECKING:
    from backend.core.conf.settings import Settings


def _get_settings() -> "Settings":
    """Lazy-load settings to avoid circular imports and ensure logging is configured first."""
    from backend.core.conf.settings import SETTINGS  # noqa: PLC0415

    return SETTINGS


def _get_structlog_formatter(*, use_json: bool = False) -> structlog.stdlib.ProcessorFormatter:
    """
    Create a ProcessorFormatter for stdlib logging that matches structlog output.

    Uses foreign_pre_chain to process logs from standard library sources
    so they have the same format as structlog logs.
    """
    # Shared processors used by both structlog and stdlib logs
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    return structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain processes logs from stdlib loggers (uvicorn, slowapi, etc.)
        foreign_pre_chain=shared_processors,
        # processors handles final rendering for all log entries
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer(),
        ],
    )


# Backwards compatibility alias for YAML config
class StructlogStyleFormatter(logging.Formatter):
    """
    Wrapper for structlog's ProcessorFormatter for use in logging config.

    This allows the YAML logging config to reference this class.
    """

    def __init__(self) -> None:
        super().__init__()
        # Create the real formatter
        self._formatter = _get_structlog_formatter(use_json=False)

    def format(self, record: logging.LogRecord) -> str:
        return self._formatter.format(record)


# Context variables for request/user tracking
# These are set by middleware (when available) and read by log processors
request_id_ctx_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return request_id_ctx_var.get()


def get_user_id() -> str | None:
    """Get the current user ID from context."""
    return user_id_ctx_var.get()


def _get_log_directory() -> Path:
    """Get the appropriate log directory based on environment.

    For Docker (prod): /app/logs/
    For local development: backend/logs/ (created if needed)
    """
    docker_logs_path = Path("/app/logs")

    # Check if we're running in Docker (the /app/logs directory exists or we can create it)
    if docker_logs_path.exists() or _get_settings().APP.ENVIRONMENT == "prod":
        return docker_logs_path

    # Local development: use a logs directory in the backend folder
    backend_dir = Path(__file__).parent.parent.parent
    local_logs_path = backend_dir / "logs"
    local_logs_path.mkdir(exist_ok=True)
    return local_logs_path


def _adjust_log_paths(config: dict[str, Any]) -> dict[str, Any]:
    """Adjust log file paths based on the current environment.

    Replaces hardcoded /app/logs/ paths with environment-appropriate paths.
    """
    log_dir = _get_log_directory()

    handlers = config.get("handlers", {})
    for handler_config in handlers.values():
        if "filename" in handler_config:
            original_path = Path(handler_config["filename"])
            # Replace /app/logs/ with the appropriate directory
            if str(original_path).startswith("/app/logs/"):
                new_path = log_dir / original_path.name
                handler_config["filename"] = str(new_path)

    return config


def _load_logging_config() -> dict[str, Any]:
    """Load logging configuration from YAML file."""
    # Determine the path to logging_config.yaml
    # It's in the backend/ directory, same level as core/
    # Path: backend/core/observability/logging.py → backend/
    backend_dir = Path(__file__).parent.parent.parent
    config_path = backend_dir / "logging_config.yaml"

    if not config_path.exists():
        error_msg = f"Logging config file not found: {config_path}"
        raise FileNotFoundError(error_msg)

    with config_path.open(encoding="utf-8") as f:
        result: dict[str, Any] = yaml.safe_load(f)
        return result


def _apply_logging_format(config: dict[str, Any], *, use_json: bool = False) -> dict[str, Any]:
    """
    Modify logging configuration to use JSON or text handlers based on format preference.

    When JSON mode is enabled:
    - Structlog loggers use passthrough formatters (structlog already outputs JSON)
    - Standard logging loggers use JSON formatters
    """
    # Loggers that use structlog (need passthrough formatter when JSON mode)
    # These loggers output JSON from structlog, so handlers should just pass through
    # Most application loggers use structlog, so root logger also uses passthrough
    structlog_loggers = {
        "app",  # Main application logger
        "backend.core.infrastructure.dbos_config",  # DBOS config uses structlog
        # Most other structlog loggers fall back to root logger
    }

    # Loggers that use standard logging only (need structured JSON formatter)
    # These loggers don't use structlog, so they need JSON formatting
    standard_loggers = {
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "sqlalchemy",
        "sqlalchemy.engine",
        "dbos",  # DBOS native logging (not structlog)
        "httpx",
        "openai",
        "slowapi",
        "watchfiles",
    }

    # Mapping of base handler names to their JSON equivalents
    # When JSON mode: use passthrough handlers (structlog already formatted)
    handler_mapping = {
        "console": "console_json",
        "file": "file_json",
        "error_file": "error_file_json",
    }

    # Mapping for non-structlog loggers that need JSON formatters
    # (e.g., uvicorn, sqlalchemy, dbos native)
    structured_handler_mapping = {
        "console": "console_json_structured",
        "file": "file_json_structured",
        "error_file": "error_file_json_structured",
    }

    # Create a reverse mapping for JSON handlers
    reverse_mapping = {v: k for k, v in handler_mapping.items()}

    def update_handlers(handlers: list[str], *, is_structlog: bool = False) -> list[str]:
        """Update handler list to use appropriate format."""
        updated = []
        for handler in handlers:
            if use_json:
                # For structlog loggers: use passthrough handlers (structlog already JSON)
                # For standard loggers: use structured JSON handlers
                if is_structlog:
                    updated.append(handler_mapping.get(handler, handler))
                else:
                    # Check if we have a structured handler, otherwise use passthrough
                    structured = structured_handler_mapping.get(handler)
                    if structured and structured in config.get("handlers", {}):
                        updated.append(structured)
                    else:
                        updated.append(handler_mapping.get(handler, handler))
            else:
                # Convert JSON handlers to text handlers
                updated.append(reverse_mapping.get(handler, handler))
        return updated

    # Update root logger handlers (use passthrough for JSON since most logs are structlog)
    if "root" in config and "handlers" in config["root"]:
        config["root"]["handlers"] = update_handlers(config["root"]["handlers"], is_structlog=True)

    # Update all logger handlers
    if "loggers" in config:
        for logger_name, logger_config in config["loggers"].items():
            if "handlers" in logger_config:
                # Determine if this logger uses structlog or standard logging
                # Standard loggers (uvicorn, sqlalchemy, dbos native) get structured JSON
                # Structlog loggers get passthrough (structlog already outputs JSON)
                is_structlog_logger = logger_name in structlog_loggers or logger_name not in standard_loggers
                logger_config["handlers"] = update_handlers(logger_config["handlers"], is_structlog=is_structlog_logger)

    return config


def add_request_id_processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """
    Structlog processor to add request_id to all log entries.

    Extracts the request ID from the context variable and adds it to the log event.

    Args:
        _logger: The wrapped logger instance (unused)
        _method_name: The name of the method called on the logger (unused)
        event_dict: The event dictionary to process

    Returns:
        Modified event dictionary with request_id added
    """
    request_id = get_request_id()
    if request_id:
        event_dict["request_id"] = request_id

    return event_dict


def add_user_id_processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """
    Structlog processor to add user_id to all log entries when available.

    Extracts the user ID from the context variable and adds it to the log event.
    """
    user_id = get_user_id()
    if user_id:
        event_dict["user_id"] = user_id
    return event_dict


def configure_logging() -> None:
    """
    Configure logging for the application.

    Uses structlog's ProcessorFormatter to ensure consistent output format
    for both structlog and stdlib loggers (uvicorn, slowapi, etc.).

    IMPORTANT: Sets up basic logging BEFORE importing settings
    to ensure all logs (including settings validation) are properly formatted.
    """
    # Step 1: Configure structlog processors first (needed by ProcessorFormatter)
    _shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_request_id_processor,
        add_user_id_processor,
    ]

    structlog.configure(
        processors=[*_shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,  # Don't cache yet - we may reconfigure
    )

    # Step 2: Set up stdlib logging with ProcessorFormatter
    # This ensures settings validation logs use structlog-style formatting
    logging.root.handlers.clear()
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_get_structlog_formatter(use_json=False))
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.DEBUG)  # Allow all levels, handlers filter

    # Step 3: Now import settings (this triggers settings validation logs)
    settings = _get_settings()

    # Step 4: Determine final format preference based on settings
    use_json = settings.APP.JSON_LOGS

    # Step 5: Reconfigure structlog with final settings and enable caching
    structlog.configure(
        processors=[*_shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Step 6: Load and configure full stdlib logging from YAML
    try:
        config = _load_logging_config()
        config = _adjust_log_paths(config)
        config = _apply_logging_format(config, use_json=use_json)
        logging.config.dictConfig(config)
    except (FileNotFoundError, yaml.YAMLError, KeyError, ValueError) as e:
        # Fallback to basic config if YAML loading fails
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=sys.stdout,
            level=getattr(logging, settings.APP.LOG_LEVEL.upper(), logging.INFO),
        )
        # Log the error using structlog (which should be configured by now)
        logger = structlog.get_logger(__name__)
        logger.warning(
            "Failed to load logging config from YAML, using basic config",
            error=str(e),
            exc_info=True,
        )
