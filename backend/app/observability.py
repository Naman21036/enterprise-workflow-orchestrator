"""Optional, failure-isolated OpenTelemetry and LangSmith setup."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Optional

from backend.app.core.config import settings

_logger = logging.getLogger(__name__)
_tracer = None
_meter = None
_workflow_counter = None
_workflow_duration = None
_safety_counter = None
_escalation_counter = None
_resume_counter = None
_resume_duration = None
_auth_counter = None


def langsmith_traceable(name: str, *, input_filter: Callable[[dict], dict], output_filter: Callable[[Any], Any], run_type: str = "chain"):
    """Trace only filtered values; operate as a transparent wrapper otherwise."""
    def decorate(function):
        try:
            from langsmith import traceable

            return traceable(
                name=name,
                run_type=run_type,
                process_inputs=input_filter,
                process_outputs=output_filter,
                dangerously_allow_filesystem=False,
            )(function)
        except Exception as exc:
            if settings.LANGSMITH_TRACING or os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}:
                _logger.warning("LangSmith tracing wrapper unavailable (%s)", type(exc).__name__)
            return function
    return decorate


@contextmanager
def span(name: str, attributes: Optional[dict[str, Any]] = None):
    tracer = _tracer
    if tracer is None:
        yield None
        return
    # Keep the workflow exception semantics intact; SDK exporters process spans
    # asynchronously and are configured outside the business execution path.
    with tracer.start_as_current_span(name, attributes=attributes or {}) as current:
        yield current


def set_attributes(current, values: dict[str, Any]) -> None:
    if current is None:
        return
    try:
        current.set_attributes({key: value for key, value in values.items() if value is not None})
    except Exception:
        pass


def record_workflow(status: str, mode: str, duration_seconds: float) -> None:
    try:
        if _workflow_counter:
            _workflow_counter.add(1, {"workflow.status": status, "execution.mode": mode})
        if _workflow_duration:
            _workflow_duration.record(max(0.0, duration_seconds), {"execution.mode": mode})
    except Exception as exc:
        _logger.warning("OpenTelemetry metric recording failed (%s)", type(exc).__name__)


def record_safety_rejection(action_type: str) -> None:
    try:
        if _safety_counter:
            _safety_counter.add(1, {"action.type": action_type})
    except Exception:
        pass


def record_escalation(reason_class: str = "human_intervention") -> None:
    try:
        if _escalation_counter:
            _escalation_counter.add(1, {"escalation.reason_class": reason_class})
    except Exception:
        pass


def record_resume(status: str, recovery: bool, duration_seconds: float) -> None:
    try:
        attributes = {"workflow.status": status, "recovery.mode": "restart" if recovery else "live"}
        if _resume_counter:
            _resume_counter.add(1, attributes)
        if _resume_duration:
            _resume_duration.record(max(0.0, duration_seconds), {"recovery.mode": attributes["recovery.mode"]})
    except Exception:
        pass


def record_auth_event(event: str) -> None:
    try:
        if _auth_counter:
            _auth_counter.add(1, {"auth.event": event})
    except Exception:
        pass


def configure_observability(app=None, engine=None) -> dict[str, Any]:
    """Initialize optional telemetry once; return status without secrets."""
    global _tracer, _meter, _workflow_counter, _workflow_duration, _safety_counter, _escalation_counter, _resume_counter, _resume_duration, _auth_counter
    status: dict[str, Any] = {"opentelemetry": "disabled", "langsmith": "disabled"}

    langsmith_enabled = settings.LANGSMITH_TRACING or os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}
    if langsmith_enabled:
        if settings.LANGSMITH_API_KEY:
            os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.LANGSMITH_ENDPOINT)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
    if langsmith_enabled:
        if not settings.LANGSMITH_API_KEY and not os.getenv("LANGSMITH_API_KEY"):
            status["langsmith"] = "enabled_but_key_missing"
            _logger.warning("LangSmith tracing requested but LANGSMITH_API_KEY is not configured")
        else:
            status["langsmith"] = "configured"
            if not (settings.LANGSMITH_PROJECT or os.getenv("LANGSMITH_PROJECT")):
                _logger.info("LangSmith tracing is enabled; SDK default project will be used")

    otel_enabled = settings.APEX_OTEL_ENABLED or os.getenv("APEX_OTEL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not otel_enabled:
        return status
    try:
        from opentelemetry import metrics, trace, propagate
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            span_exporter = OTLPSpanExporter(endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or f"{endpoint.rstrip('/')}/v1/traces")
            metric_exporter = OTLPMetricExporter(endpoint=os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT") or f"{endpoint.rstrip('/')}/v1/metrics")
        else:
            span_exporter = ConsoleSpanExporter()
            metric_exporter = ConsoleMetricExporter()

        resource = Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "apex-automation"),
            "service.version": "1.0.0",
        })
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(provider)
        propagate.set_global_textmap(CompositePropagator([
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ]))
        _tracer = trace.get_tracer("apex-automation")
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000)
        metric_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(metric_provider)
        _meter = metrics.get_meter("apex-automation")
        _workflow_counter = _meter.create_counter("apex.workflow.executions", unit="{run}")
        _workflow_duration = _meter.create_histogram("apex.workflow.duration", unit="s")
        _safety_counter = _meter.create_counter("apex.safety.rejections", unit="{rejection}")
        _escalation_counter = _meter.create_counter("apex.workflow.escalations", unit="{escalation}")
        _resume_counter = _meter.create_counter("apex.handoff.resumes", unit="{resume}")
        _resume_duration = _meter.create_histogram("apex.handoff.resume.duration", unit="s")
        _auth_counter = _meter.create_counter("apex.auth.events", unit="{event}")

        if app is not None:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
            except Exception as exc:
                _logger.warning("FastAPI instrumentation unavailable (%s)", type(exc).__name__)
        if engine is not None:
            try:
                from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
                SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, enable_commenter=False)
            except Exception as exc:
                _logger.warning("SQLAlchemy instrumentation unavailable (%s)", type(exc).__name__)
        status["opentelemetry"] = "console_exporter" if not endpoint else "otlp_configured"
    except Exception as exc:
        _tracer = None
        status["opentelemetry"] = f"unavailable:{type(exc).__name__}"
        _logger.warning("OpenTelemetry initialization failed; workflow execution remains available (%s)", type(exc).__name__)
    return status


def current_trace_id() -> Optional[str]:
    try:
        from opentelemetry import trace
        context = trace.get_current_span().get_span_context()
        return f"{context.trace_id:032x}" if context.is_valid else None
    except Exception:
        return None


def langsmith_context(metadata: dict[str, Any]):
    """Build a best-effort context manager with low-risk correlation metadata."""
    try:
        from langsmith import tracing_context
        return tracing_context(
            project_name=os.getenv("LANGSMITH_PROJECT") or None,
            metadata=metadata,
        )
    except Exception:
        from contextlib import nullcontext
        return nullcontext()
