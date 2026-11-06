from typing import Any

import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from assistant import observability
from assistant.config import Settings
from assistant.observability import otlp_headers


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, assistant_mcp_token="t", customer_ref_secret="s", **values)  # pyright: ignore[reportCallIssue]


def test_headers_are_read_the_way_otel_writes_them() -> None:
    # Langfuse's Basic auth is base64, padded with "=": only the first "=" splits.
    raw = "Authorization=Basic cGs6c2s=,x-langfuse-ingestion-version=4"
    assert otlp_headers(raw) == {
        "Authorization": "Basic cGs6c2s=",
        "x-langfuse-ingestion-version": "4",
    }


def test_without_an_endpoint_nothing_is_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("logfire was configured")

    monkeypatch.setattr(observability.logfire, "configure", refuse)
    assert observability.configure(settings(), FastAPI()) is False


def test_traces_go_to_the_endpoint_and_nowhere_else(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Exporter:
        def __init__(self, *, endpoint: str, headers: dict[str, str]) -> None:
            seen["exporter"] = {"endpoint": endpoint, "headers": headers}

        def shutdown(self) -> None: ...

    def record(name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> None:
            seen[name] = (args, kwargs)

        return call

    monkeypatch.setattr(observability, "OTLPSpanExporter", Exporter)
    for name in ("configure", "instrument_pydantic_ai", "instrument_fastapi"):
        monkeypatch.setattr(observability.logfire, name, record(name))
    app = FastAPI()
    endpoint = "https://cloud.langfuse.com/api/public/otel/v1/traces"

    traced = observability.configure(
        settings(
            otel_traces_endpoint=endpoint,
            otel_headers="Authorization=Basic cGs6c2s=,x-langfuse-ingestion-version=4",
        ),
        app,
    )

    assert traced is True
    assert seen["exporter"] == {
        "endpoint": endpoint,
        "headers": {"Authorization": "Basic cGs6c2s=", "x-langfuse-ingestion-version": "4"},
    }
    _, options = seen["configure"]
    assert options["send_to_logfire"] is False
    [processor] = options["additional_span_processors"]
    assert isinstance(processor, BatchSpanProcessor)
    # What customers write is personal data: out of the traces unless asked for.
    assert seen["instrument_pydantic_ai"] == ((), {"include_content": False})
    # Which arguments reach the request spans: tests/unit/test_traces_exported.py.
    (instrumented,), options = seen["instrument_fastapi"]
    assert instrumented is app
    # The widget's polls would spend Langfuse's units: tests/unit/test_traces_exported.py.
    assert options["excluded_urls"] == "/health,/web/"


async def test_without_an_endpoint_a_flush_is_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("logfire was flushed")

    monkeypatch.setattr(observability.logfire, "force_flush", refuse)
    await observability.flusher(settings())()


async def test_with_an_endpoint_a_flush_sends_the_finished_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flushed: list[str] = []

    def force_flush(*args: Any, **kwargs: Any) -> bool:
        flushed.append("now")
        return True

    monkeypatch.setattr(observability.logfire, "force_flush", force_flush)
    endpoint = "https://cloud.langfuse.com/api/public/otel/v1/traces"
    await observability.flusher(settings(otel_traces_endpoint=endpoint))()
    assert flushed == ["now"]
