"""What Langfuse would receive: the real trace configuration, run in a fresh
process as in production. OpenTelemetry takes one tracer provider per process,
and the test session has set its own.

`python -m tests.trace_probe` prints every exported span as JSON. The OTLP
exporter is swapped for one that keeps the spans, so nothing leaves the
machine, and the database is a closed port.
"""

import asyncio
import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

import httpx
import logfire
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from pydantic_ai.models.test import TestModel

from assistant import observability
from assistant.config import Settings
from assistant.conversation.turn import tracer
from assistant.main import create_app

# What no trace may carry. ASCII, and no word Logfire's scrubber looks for, so
# that a leak shows: Logfire escapes "ş", and redacts any value saying "password".
DATABASE_PASSWORD = "x7Hq2Lw9Zr4K"
CUSTOMER_TEXT = "Merhaba, ben Elif Kaya"
VISITOR_ID = "0b9a6a3c-5c8e-4c1e-9d6e-2f1f5b7c9a10"
# What Langfuse groups a turn's trace by.
CONVERSATION = "7"

exported: list[ReadableSpan] = []


class KeptExporter(SpanExporter):
    """Stands in for the OTLP exporter: keeps what it would send."""

    def __init__(self, **options: Any) -> None:
        pass

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        exported.extend(spans)
        return SpanExportResult.SUCCESS


async def requests(settings: Settings) -> None:
    """A customer's Telegram message and a widget poll, as they arrive."""
    app = create_app(settings, model=TestModel())
    async with app.router.lifespan_context(app):
        # Both fail at the database, once their arguments are read.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 4242, "type": "private"},
                    "date": 1_790_000_000,
                    "text": CUSTOMER_TEXT,
                },
            }
            await client.post(
                "/telegram/webhook",
                json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
            )
            await client.get("/web/demo/messages", headers={"X-Visitor-Id": VISITOR_ID})


def turn() -> None:
    """A turn's span, and a model call inside it, the way TurnProcessor makes them."""
    with (
        observability.tagged({"langfuse.session.id": CONVERSATION}),
        tracer.start_as_current_span("turn") as span,
    ):
        span.set_attribute("langfuse.session.id", CONVERSATION)
        with tracer.start_as_current_span("model call"):
            pass


def main() -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=f"postgresql+asyncpg://assistant:{DATABASE_PASSWORD}@127.0.0.1:9/assistant",
        assistant_mcp_token="t",
        customer_ref_secret="s",
        telegram_bot_token="123456:secret-token",
        telegram_webhook_secret="webhook-secret",
        otel_traces_endpoint="https://langfuse.invalid/api/public/otel/v1/traces",
        otel_headers="Authorization=Basic cGs6c2s=,x-langfuse-ingestion-version=4",
    )
    with patch.object(observability, "OTLPSpanExporter", KeptExporter):
        asyncio.run(requests(settings))
    turn()
    logfire.force_flush()
    spans = [{"name": span.name, "attributes": dict(span.attributes or {})} for span in exported]
    print(json.dumps(spans, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
