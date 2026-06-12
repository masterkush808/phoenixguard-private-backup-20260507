from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.urllib import URLLibInstrumentor
    from opentelemetry.sdk.resources import (
        Resource,
        SERVICE_NAME as _otel_service_name_key,
        SERVICE_VERSION as _otel_service_version_key,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except Exception:  # pragma: no cover - optional tracing dependency path
    trace = None
    OTLPSpanExporter = None
    FastAPIInstrumentor = None
    RequestsInstrumentor = None
    URLLibInstrumentor = None
    Resource = None
    _otel_service_name_key = "service.name"
    _otel_service_version_key = "service.version"
    TracerProvider = None
    BatchSpanProcessor = None

_LOGGER = logging.getLogger(__name__)
_LOCK = threading.Lock()
_tracing_initialized = False
_urllib_instrumented = False
_requests_instrumented = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_text(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            value = str(raw).strip()
            if value:
                return value
    return default


def _parse_headers(value: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    raw = str(value or "").strip()
    if not raw:
        return headers
    for chunk in raw.replace(";", ",").split(","):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        key, header_value = piece.split("=", 1)
        key = key.strip()
        header_value = header_value.strip()
        if key and header_value:
            headers[key] = header_value
    return headers


def _instrument_client_side_tracers() -> None:
    global _urllib_instrumented, _requests_instrumented

    if URLLibInstrumentor is not None and not _urllib_instrumented:
        try:
            URLLibInstrumentor().instrument()
            _urllib_instrumented = True
        except Exception as exc:  # pragma: no cover - tracing should not break startup
            _LOGGER.debug("Unable to instrument urllib for tracing: %s", exc)

    if RequestsInstrumentor is not None and not _requests_instrumented:
        try:
            RequestsInstrumentor().instrument()
            _requests_instrumented = True
        except Exception as exc:  # pragma: no cover - tracing should not break startup
            _LOGGER.debug("Unable to instrument requests for tracing: %s", exc)


def configure_tracing(service_name: str, service_version: str | None = None) -> bool:
    global _tracing_initialized

    with _LOCK:
        if _tracing_initialized:
            _instrument_client_side_tracers()
            return True

        if _env_bool("OTEL_SDK_DISABLED") or _env_bool("PHOENIXGUARD_TRACING_DISABLED"):
            _tracing_initialized = True
            return False

        if (
            trace is None
            or OTLPSpanExporter is None
            or Resource is None
            or TracerProvider is None
            or BatchSpanProcessor is None
        ):
            _LOGGER.warning("OpenTelemetry tracing packages are unavailable; continuing without tracing.")
            _tracing_initialized = True
            return False

        resolved_service_name = _env_text("PHOENIXGUARD_TRACE_SERVICE_NAME", "OTEL_SERVICE_NAME", default=service_name)
        resolved_service_version = _env_text(
            "PHOENIXGUARD_TRACE_SERVICE_VERSION",
            "OTEL_SERVICE_VERSION",
            default=str(service_version or "1.0.0"),
        )
        endpoint = _env_text(
            "PHOENIXGUARD_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            default="http://localhost:4318/v1/traces",
        )
        headers = _parse_headers(
            _env_text("PHOENIXGUARD_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS", default="")
        )

        provider = TracerProvider(
            resource=Resource.create(
                {
                    _otel_service_name_key: resolved_service_name,
                    _otel_service_version_key: resolved_service_version,
                    "service.namespace": "phoenixguard",
                }
            )
        )
        exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers or None)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        atexit.register(provider.shutdown)
        _tracing_initialized = True

    _instrument_client_side_tracers()
    return True


def instrument_fastapi_app(app: Any) -> bool:
    if FastAPIInstrumentor is None:
        return False
    if app is None:
        return False
    state = getattr(app, "state", None)
    if state is not None and getattr(state, "_phoenixguard_tracing_instrumented", False):
        return True

    if not configure_tracing("phoenixguard-mobile-api"):
        return False
    try:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=trace.get_tracer_provider() if trace is not None else None)
        if state is not None:
            setattr(state, "_phoenixguard_tracing_instrumented", True)
        return True
    except Exception as exc:  # pragma: no cover - tracing should not break startup
        _LOGGER.warning("Failed to instrument FastAPI app for tracing: %s", exc)
        return False
