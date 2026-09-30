from backend.app.core.config import settings
from backend.app import observability
from backend.app.observability import configure_observability, langsmith_traceable


def test_observability_can_be_disabled_without_external_services(monkeypatch):
    monkeypatch.setattr(settings, "LANGSMITH_TRACING", False)
    monkeypatch.setattr(settings, "APEX_OTEL_ENABLED", False)
    monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    status = configure_observability()
    assert status == {"opentelemetry": "disabled", "langsmith": "disabled"}


def test_langsmith_wrapper_is_transparent_when_tracing_is_disabled():
    @langsmith_traceable("test.safe_wrapper", input_filter=lambda value: {}, output_filter=lambda value: {})
    def operation(value):
        return value + 1

    assert operation(4) == 5


def test_sync_span_context_manager_can_wrap_async_work(monkeypatch):
    monkeypatch.setattr(observability, "_tracer", None)

    async def operation():
        with observability.span("apex.test.async_work", {"test.safe": True}):
            return "completed"

    import asyncio

    assert asyncio.run(operation()) == "completed"
