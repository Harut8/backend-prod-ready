"""
OpenTelemetry Distributed Tracing.

Provides distributed tracing for:
- HTTP requests through FastAPI
- Database queries through SQLAlchemy
- Redis operations
- Cross-service request correlation

Usage:
    from backend.core.observability.tracing import setup_tracing

    # In main.py
    setup_tracing(app)

Environment Variables:
    OTEL_SERVICE_NAME: Service name for tracing (default: app name from settings)
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP collector endpoint (e.g., http://localhost:4317)
    OTEL_EXPORTER_OTLP_HEADERS: Optional headers for OTLP exporter
    OTEL_TRACES_SAMPLER: Sampling strategy (always_on, always_off, parentbased_traceidratio)
    OTEL_TRACES_SAMPLER_ARG: Sampling ratio (0.0-1.0 for traceidratio sampler)
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

import structlog

from backend.core.conf.settings import SETTINGS


if TYPE_CHECKING:
    from fastapi import FastAPI


logger = structlog.get_logger(__name__)


class _OtelState:
    """Container for lazy-loaded OpenTelemetry state (avoids global statements)."""

    trace_module: Any = None
    available: bool | None = None


def _ensure_otel_available() -> bool:
    """
    Check if OpenTelemetry is available and cache the result.

    Returns:
        True if OpenTelemetry packages are installed, False otherwise.
    """
    if _OtelState.available is not None:
        return _OtelState.available

    try:
        from opentelemetry import trace  # noqa: PLC0415

        _OtelState.trace_module = trace
        _OtelState.available = True
    except ImportError:
        _OtelState.available = False
        logger.debug("OpenTelemetry packages not available")

    return _OtelState.available


def _extract_trace_context() -> dict[str, str]:
    """
    Extract trace_id and span_id from the current span context.

    Returns:
        Dict with trace_id and span_id if valid, empty dict otherwise.
    """
    if not _ensure_otel_available() or _OtelState.trace_module is None:
        return {}

    try:
        current_span = _OtelState.trace_module.get_current_span()
        span_context = current_span.get_span_context()

        if span_context.is_valid:
            return {
                "trace_id": format(span_context.trace_id, "032x"),
                "span_id": format(span_context.span_id, "016x"),
            }
    except Exception:
        logger.exception("Failed to extract trace context")

    return {}


def setup_tracing(app: FastAPI) -> None:
    """
    Set up OpenTelemetry distributed tracing for the application.

    Only enabled if TRACING_ENABLED=true in environment/settings.

    Configures tracing for:
    - FastAPI HTTP requests
    - SQLAlchemy database queries
    - Redis cache operations

    Traces are exported to an OTLP collector (Jaeger, Zipkin, etc.)
    when OTEL_EXPORTER_OTLP_ENDPOINT is set.
    """
    if not SETTINGS.TRACING.TRACING_ENABLED:
        logger.info("OpenTelemetry tracing disabled (TRACING_ENABLED=false)")
        return

    otlp_endpoint = SETTINGS.TRACING.OTEL_EXPORTER_OTLP_ENDPOINT
    if not otlp_endpoint:
        logger.warning(
            "OpenTelemetry tracing enabled but OTEL_EXPORTER_OTLP_ENDPOINT not set",
            hint="Set OTEL_EXPORTER_OTLP_ENDPOINT to export traces",
        )
        return

    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # noqa: PLC0415
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415
        from opentelemetry.instrumentation.redis import RedisInstrumentor  # noqa: PLC0415
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # noqa: PLC0415
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
        from opentelemetry.sdk.trace.sampling import (  # noqa: PLC0415
            ALWAYS_OFF,
            ALWAYS_ON,
            ParentBasedTraceIdRatio,
        )
    except ImportError as e:
        logger.warning(
            "OpenTelemetry packages not installed - tracing disabled",
            error=str(e),
            hint="Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi",
        )
        return

    # Update module reference
    _OtelState.trace_module = trace
    _OtelState.available = True

    # Configure service resource
    service_name = SETTINGS.TRACING.OTEL_SERVICE_NAME or SETTINGS.APP.APP_NAME
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            SERVICE_VERSION: "1.0.0",
            "deployment.environment": SETTINGS.APP.ENVIRONMENT,
        }
    )

    # Configure sampler
    sampler_name = SETTINGS.TRACING.OTEL_TRACES_SAMPLER
    sampler_arg = SETTINGS.TRACING.OTEL_TRACES_SAMPLER_ARG
    sampler = _build_sampler(sampler_name, sampler_arg, ALWAYS_ON, ALWAYS_OFF, ParentBasedTraceIdRatio)

    # Create and configure tracer provider
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=otlp_endpoint, insecure=SETTINGS.TRACING.OTEL_EXPORTER_OTLP_INSECURE),
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    # Instrument frameworks
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics,ready,live")
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()

    logger.info(
        "OpenTelemetry tracing enabled",
        service_name=service_name,
        endpoint=otlp_endpoint,
        sampler=sampler_name,
        sample_rate=sampler_arg if sampler_name == "parentbased_traceidratio" else "N/A",
    )


def _build_sampler(
    sampler_name: str,
    sampler_arg: float,
    always_on: Any,
    always_off: Any,
    parent_based_ratio: type,
) -> Any:
    """Build the appropriate sampler based on configuration."""
    if sampler_name == "always_on":
        return always_on
    if sampler_name == "always_off":
        return always_off
    return parent_based_ratio(sampler_arg)


def get_current_trace_context() -> dict[str, Any]:
    """
    Get the current trace context for logging or propagation.

    Returns dict with trace_id and span_id if tracing is active.
    """
    return _extract_trace_context()


def create_trace_context_processor() -> Any:
    """
    Create a structlog processor that adds trace context to logs.

    Usage:
        import structlog
        structlog.configure(
            processors=[
                create_trace_context_processor(),
                # ... other processors
            ]
        )

    Returns:
        A structlog processor function, or None if OpenTelemetry is unavailable.
    """
    if not _ensure_otel_available():
        logger.debug("OpenTelemetry not available - trace context processor disabled")
        return None

    def add_trace_context(
        _logger: Any,
        _method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Add trace context to log entries."""
        context = _extract_trace_context()
        if context:
            event_dict.update(context)
        return event_dict

    return add_trace_context


def create_span(name: str, attributes: dict[str, Any] | None = None) -> Any:
    """
    Create a new span for custom instrumentation.

    Usage:
        with create_span("my_operation", {"user_id": user_id}) as span:
            # Do work
            span.set_attribute("result", "success")

    Args:
        name: Span name
        attributes: Optional attributes to set on the span

    Returns:
        Context manager for the span (no-op if OpenTelemetry unavailable)
    """
    if not _ensure_otel_available() or _OtelState.trace_module is None:
        return nullcontext()

    tracer = _OtelState.trace_module.get_tracer(__name__)
    return tracer.start_as_current_span(name, attributes=attributes)


def record_exception(exception: Exception) -> None:
    """
    Record an exception on the current span.

    Call this in exception handlers to add exception details to traces.
    """
    if not _ensure_otel_available() or _OtelState.trace_module is None:
        return

    try:
        current_span = _OtelState.trace_module.get_current_span()
        current_span.record_exception(exception)
        current_span.set_status(
            _OtelState.trace_module.Status(_OtelState.trace_module.StatusCode.ERROR, str(exception))
        )
    except Exception:
        logger.exception("Failed to record exception on span")
