"""Traces (spec §7.10): the turns, Pydantic AI's model calls and FastAPI's
requests, over OTLP/HTTP to Langfuse Cloud EU, and none to Logfire itself.

Two things stay out. httpx is not instrumented, since Telegram's URLs carry
the bot token. And what customers wrote is left out unless TRACE_CONTENT is
set, since it is personal data and Langfuse keeps it for 30 days.
"""

from collections.abc import Generator
from contextlib import contextmanager
from urllib.parse import unquote

import logfire
from fastapi import FastAPI
from opentelemetry import baggage, context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from assistant.config import Settings

SERVICE = "rezerve-assistant"


def otlp_headers(raw: str) -> dict[str, str]:
    """OTEL_EXPORTER_OTLP_HEADERS' format: "key=value,key=value", values
    URL-encoded. Only the first "=" of a pair splits it: base64 ends in "="."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        key, found, value = pair.partition("=")
        if found and key.strip():
            headers[key.strip()] = unquote(value.strip())
    return headers


def configure(settings: Settings, app: FastAPI) -> bool:
    """Send traces, if there is somewhere to send them; returns whether it will."""
    if settings.otel_traces_endpoint is None:
        return False
    raw = settings.otel_headers.get_secret_value() if settings.otel_headers else ""
    exporter = OTLPSpanExporter(endpoint=settings.otel_traces_endpoint, headers=otlp_headers(raw))
    logfire.configure(
        send_to_logfire=False,
        service_name=SERVICE,
        console=False,
        metrics=False,
        additional_span_processors=[BatchSpanProcessor(exporter)],
    )
    logfire.instrument_pydantic_ai(include_content=settings.trace_content)
    logfire.instrument_fastapi(app, excluded_urls="/health")
    return True


@contextmanager
def tagged(values: dict[str, str]) -> Generator[None]:
    """Values every span started inside carries, the model calls' too:
    Langfuse reads a trace's session and user from any of its spans."""
    current = context.get_current()
    for key, value in values.items():
        current = baggage.set_baggage(key, value, current)
    token = context.attach(current)
    try:
        yield
    finally:
        context.detach(token)
