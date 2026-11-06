"""Traces (spec §7.10): the turns, Pydantic AI's model calls and FastAPI's
requests, over OTLP/HTTP to Langfuse Cloud EU, and none to Logfire itself.

Three things stay out. httpx is not instrumented, since Telegram's URLs carry
the bot token. What customers wrote is left out unless TRACE_CONTENT is set,
since it is personal data and Langfuse keeps it for 30 days. And no request
span carries its endpoint's arguments: they hold the settings, the database
URL among them, what a customer wrote, and the widget's visitor ids.

The widget's requests are not traced at all. It polls every second while a
reply is coming, and every ten seconds while the owner holds a conversation:
those would spend the 50k units a month Langfuse's free plan has for turns.
"""

import asyncio
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote

import logfire
from fastapi import FastAPI, Request, WebSocket
from opentelemetry import baggage, context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from assistant.config import Settings

SERVICE = "rezerve-assistant"
# What Langfuse groups traces by. Logfire's scrubber would redact the session
# for its name, but here it names a conversation, not a login.
LANGFUSE_KEYS = frozenset({"langfuse.session.id", "langfuse.user.id"})
# Requests left out of traces: regular expressions, searched in each URL.
UNTRACED = "/health,/web/"

# Sends the spans finished so far, before a request answers.
type Flush = Callable[[], Awaitable[None]]


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
        scrubbing=logfire.ScrubbingOptions(callback=_keep_langfuse_keys),
    )
    logfire.instrument_pydantic_ai(include_content=settings.trace_content)
    logfire.instrument_fastapi(app, excluded_urls=UNTRACED, request_attributes_mapper=_no_arguments)
    return True


def flusher(settings: Settings) -> Flush:
    """What sends a request's finished spans before it answers. Cloud Run
    gives an instance CPU only while it answers a request: spans left to the
    exporter's own timer wait for the next request, or go with the instance."""
    if settings.otel_traces_endpoint is None:
        return _nothing
    return _flush


async def _flush() -> None:
    # It waits on Langfuse's answer: off the event loop.
    await asyncio.to_thread(logfire.force_flush)


async def _nothing() -> None:
    return None


def _keep_langfuse_keys(match: logfire.ScrubMatch) -> Any:
    """Everything the scrubber finds is redacted, except Langfuse's own keys."""
    return match.value if match.path[-1] in LANGFUSE_KEYS else None


def _no_arguments(request: Request | WebSocket, attributes: dict[str, Any]) -> None:
    """Logfire would put every endpoint argument on the request's span."""
    return None


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
