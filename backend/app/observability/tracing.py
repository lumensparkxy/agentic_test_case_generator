import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from .logging import get_log_context

_TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-fA-F]{2})-(?P<trace_id>[0-9a-fA-F]{32})-(?P<span_id>[0-9a-fA-F]{16})-(?P<trace_flags>[0-9a-fA-F]{2})(?:-.+)?$"
)
_TRACING_CONFIGURED = False


@dataclass(frozen=True)
class TracingSetupResult:
    enabled: bool
    instrumented: bool
    reason: str


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_resource_attributes(raw_value: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for item in raw_value.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            attributes[key] = value
    return attributes


def extract_trace_id_from_traceparent(traceparent: Optional[str]) -> Optional[str]:
    value = str(traceparent or "").strip()
    match = _TRACEPARENT_PATTERN.match(value)
    if not match:
        return None

    trace_id = match.group("trace_id").lower()
    span_id = match.group("span_id").lower()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id


def get_current_trace_id() -> Optional[str]:
    context_trace_id = get_log_context().get("trace_id")
    if context_trace_id:
        return str(context_trace_id)

    try:
        from opentelemetry import trace  # type: ignore
    except Exception:
        return None

    try:
        span_context = trace.get_current_span().get_span_context()
    except Exception:
        return None

    if not getattr(span_context, "is_valid", False):
        return None
    return f"{span_context.trace_id:032x}"


def resolve_trace_id(traceparent: Optional[str] = None) -> Optional[str]:
    return extract_trace_id_from_traceparent(traceparent) or get_current_trace_id()


def configure_tracing(app: Any) -> TracingSetupResult:
    """Optionally configure OpenTelemetry without making it a hard runtime dependency."""
    global _TRACING_CONFIGURED
    if not _env_bool("OTEL_ENABLED", default=False):
        return TracingSetupResult(enabled=False, instrumented=False, reason="disabled")

    if _TRACING_CONFIGURED:
        return TracingSetupResult(enabled=True, instrumented=True, reason="already_configured")

    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional package installation
        logging.warning("OpenTelemetry tracing requested but dependencies are unavailable: %s", exc)
        return TracingSetupResult(enabled=True, instrumented=False, reason="missing_dependencies")

    service_name = (
        os.getenv("OTEL_SERVICE_NAME")
        or os.getenv("SERVICE_NAME")
        or "agentic-test-case-generator-api"
    )
    resource_attributes = {
        "service.name": service_name,
        "service.version": os.getenv("SERVICE_VERSION", "dev"),
        "deployment.environment": os.getenv("ENVIRONMENT", "local"),
    }
    resource_attributes.update(_parse_resource_attributes(os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")))

    provider = TracerProvider(resource=Resource.create(resource_attributes))
    endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    _TRACING_CONFIGURED = True
    logging.info(
        "OpenTelemetry tracing configured",
        extra={"event": "observability.tracing.configured", "otel_service_name": service_name},
    )
    return TracingSetupResult(enabled=True, instrumented=True, reason="configured")
