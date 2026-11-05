"""Rezerve's "kapora received" event (spec §6.5), through the whole service in
process: signed, inside its window, told to the customer once, on their
channel."""

import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from assistant.channels.base import InboundMessage
from assistant.config import Settings
from assistant.conversation.store import Store, TurnResult
from assistant.events import signature
from assistant.identity import customer_ref
from assistant.main import create_app
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices
from tests.fakes.models import Script
from tests.fakes.rezerve import FakeRezerve
from tests.fakes.telegram import FakeTelegram

SECRET = "event-secret"
CHAT = 4242
REF = customer_ref("s", "telegram", str(CHAT))
CONFIRMED = "Kaporanız alındı, randevunuz onaylandı ✅ Cilt Bakımı · Cumartesi 7 Kasım 15:30"


def confirmed(booking_id: str = "b-1", ref: str = REF) -> dict[str, Any]:
    return {
        "type": "booking.confirmed",
        "booking_id": booking_id,
        "business_slug": "demo",
        "customer_ref": ref,
        "service_name": "Cilt Bakımı",
        "local_label": "Cumartesi 7 Kasım 15:30",
    }


class Rezerve:
    """Rezerve's side: events, signed the way lib/assistant/events.ts signs them."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient, telegram: FakeTelegram) -> None:
        self.app = app
        self.client = client
        self.telegram = telegram

    async def send(
        self,
        event: dict[str, Any],
        *,
        secret: str = SECRET,
        at: float | None = None,
        signed: bool = True,
    ) -> httpx.Response:
        body = json.dumps(event).encode()
        timestamp = str(int(at if at is not None else time.time()))
        headers = {"Content-Type": "application/json", "X-Rezerve-Timestamp": timestamp}
        if signed:
            headers["X-Rezerve-Signature"] = signature(secret, timestamp, body)
        return await self.client.post("/events/rezerve", content=body, headers=headers)

    async def settle(self) -> None:
        services = cast(AppServices, self.app.state.services)
        await cast(LocalTaskQueue, services.queue).drain()


@pytest.fixture
def event_overrides() -> dict[str, Any]:
    return {}


@pytest.fixture
async def rezerve(
    database_url: str, store: Store, event_overrides: dict[str, Any]
) -> AsyncIterator[Rezerve]:
    fake = FakeRezerve()
    telegram = FakeTelegram()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        telegram_bot_token="123456:secret-token",
        telegram_webhook_secret="webhook-secret",
        **{"assistant_event_secret": SECRET} | event_overrides,
    )
    app = create_app(
        settings,
        model=Script().model,
        open_rezerve=lambda slug, ref: fake.session(),
        telegram_transport=telegram.transport,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Rezerve(app, client, telegram)


async def talked_on_telegram(store: Store) -> None:
    """A Telegram customer whose conversation is answered and waiting."""
    now = datetime.now(UTC)
    recorded = await store.record_inbound(
        InboundMessage("demo", "telegram", str(CHAT), "update-1", "merhaba"), REF, now
    )
    assert recorded.seq is not None
    await store.commit_turn(
        recorded.conversation_id, TurnResult(processed=[recorded.seq], replies=[]), now
    )


async def test_a_paid_kapora_is_told_to_the_customer_on_their_channel(
    rezerve: Rezerve, store: Store
) -> None:
    await talked_on_telegram(store)
    response = await rezerve.send(confirmed())
    await rezerve.settle()
    assert response.status_code == 202
    assert rezerve.telegram.texts(CHAT) == [CONFIRMED]


async def test_the_same_event_twice_is_told_once(rezerve: Rezerve, store: Store) -> None:
    await talked_on_telegram(store)
    await rezerve.send(confirmed())
    await rezerve.send(confirmed())
    await rezerve.settle()
    assert rezerve.telegram.texts(CHAT) == [CONFIRMED]


@pytest.mark.parametrize(
    "tamper",
    ["unsigned", "wrong secret", "five minutes and more old", "from the future"],
)
async def test_only_a_fresh_event_signed_with_the_secret_is_heard(
    rezerve: Rezerve, store: Store, tamper: str
) -> None:
    await talked_on_telegram(store)
    sends = {
        "unsigned": lambda: rezerve.send(confirmed(), signed=False),
        "wrong secret": lambda: rezerve.send(confirmed(), secret="guessed"),
        "five minutes and more old": lambda: rezerve.send(confirmed(), at=time.time() - 301),
        "from the future": lambda: rezerve.send(confirmed(), at=time.time() + 301),
    }
    response = await sends[tamper]()
    await rezerve.settle()
    assert response.status_code == 401
    assert rezerve.telegram.calls == []


async def test_a_customer_with_no_conversation_has_nothing_to_hear(rezerve: Rezerve) -> None:
    response = await rezerve.send(confirmed(ref=customer_ref("s", "telegram", "999")))
    assert response.status_code == 202
    assert rezerve.telegram.calls == []


async def test_an_event_of_another_kind_is_ignored(rezerve: Rezerve, store: Store) -> None:
    await talked_on_telegram(store)
    response = await rezerve.send({"type": "booking.refunded", "booking_id": "b-1"})
    assert response.status_code == 202
    assert rezerve.telegram.calls == []


@pytest.mark.parametrize("event_overrides", [{"assistant_event_secret": None}])
async def test_without_a_secret_there_is_no_endpoint(rezerve: Rezerve) -> None:
    assert (await rezerve.send(confirmed())).status_code == 404


async def test_a_signed_event_missing_its_details_is_refused(rezerve: Rezerve) -> None:
    response = await rezerve.send({"type": "booking.confirmed", "booking_id": "b-1"})
    assert response.status_code == 422
