"""The Telegram webhook, through the whole service in process: the app and
its lifespan, the local queue, Postgres, the fake Rezerve, a scripted model,
and a fake Bot API."""

from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from assistant.channels.telegram_webhook import NO_BUSINESS, TEXT_ONLY, WELCOME
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.main import create_app
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices
from tests.fakes.models import Script, says
from tests.fakes.rezerve import FakeRezerve
from tests.fakes.telegram import FakeTelegram

SECRET = "webhook-secret"
CHAT = 4242


class Bot:
    """Telegram's side of the webhook: updates, posted the way the Bot API does."""

    def __init__(
        self, app: FastAPI, client: httpx.AsyncClient, telegram: FakeTelegram, script: Script
    ) -> None:
        self.app = app
        self.client = client
        self.telegram = telegram
        self.script = script
        self._update_id = 1000

    async def deliver(
        self, message: dict[str, Any], *, secret: str | None = SECRET, update_id: int | None = None
    ) -> httpx.Response:
        if update_id is None:
            self._update_id += 1
            update_id = self._update_id
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
        return await self.client.post(
            "/telegram/webhook", json={"update_id": update_id, "message": message}, headers=headers
        )

    async def write(
        self,
        text: str,
        *,
        chat_type: str = "private",
        secret: str | None = SECRET,
        update_id: int | None = None,
    ) -> httpx.Response:
        message = {
            "message_id": 1,
            "from": {"id": CHAT, "is_bot": False, "first_name": "Elif"},
            "chat": {"id": CHAT, "type": chat_type},
            "date": 1_790_000_000,
            "text": text,
        }
        return await self.deliver(message, secret=secret, update_id=update_id)

    async def settle(self) -> None:
        services = cast(AppServices, self.app.state.services)
        await cast(LocalTaskQueue, services.queue).drain()


@pytest.fixture
def bot_overrides() -> dict[str, Any]:
    """Settings a test changes, by parametrizing this fixture's name."""
    return {}


@pytest.fixture
async def bot(database_url: str, store: Store, bot_overrides: dict[str, Any]) -> AsyncIterator[Bot]:
    fake = FakeRezerve()
    telegram = FakeTelegram()
    script = Script()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        **{"telegram_bot_token": "123456:secret-token", "telegram_webhook_secret": SECRET}
        | bot_overrides,
    )
    app = create_app(
        settings,
        model=script.model,
        open_rezerve=lambda slug, ref: fake.session(),
        telegram_transport=telegram.transport,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Bot(app, client, telegram, script)


async def test_the_start_link_binds_the_chat_to_its_business(bot: Bot, store: Store) -> None:
    # t.me/<bot>?start=demo arrives as "/start demo" (spec §7.9).
    response = await bot.write("/start demo")
    assert response.status_code == 200
    assert bot.telegram.texts(CHAT) == [WELCOME]
    assert await store.telegram_business(CHAT) == "demo"


async def test_a_message_after_the_start_link_is_answered_in_the_chat(bot: Bot) -> None:
    bot.script.add(says("Merhaba! Yarın için hangi hizmete bakalım?"))
    await bot.write("/start demo")
    await bot.write("yarın yer var mı?")
    await bot.settle()
    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba! Yarın için hangi hizmete bakalım?"]


async def test_a_redelivered_update_is_answered_once(bot: Bot) -> None:
    # Telegram resends an update it did not see acknowledged; update_id is
    # unique across the bot, a message_id only within its chat (spec §7.2).
    bot.script.add(says("Merhaba!"))
    await bot.write("/start demo")
    await bot.write("merhaba", update_id=5000)
    await bot.write("merhaba", update_id=5000)
    await bot.settle()
    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba!"]


async def test_a_chat_that_never_started_from_a_business_is_told_where_to_begin(
    bot: Bot, store: Store
) -> None:
    for text in ("merhaba", "/start", "/start Not A Slug!"):
        await bot.write(text)
    assert bot.telegram.texts(CHAT) == [NO_BUSINESS] * 3
    assert await store.telegram_business(CHAT) is None


async def test_only_written_messages_are_understood(bot: Bot) -> None:
    await bot.write("/start demo")
    sticker = {
        "message_id": 2,
        "chat": {"id": CHAT, "type": "private"},
        "date": 1_790_000_000,
        "sticker": {"file_id": "x", "width": 512, "height": 512},
    }
    await bot.deliver(sticker)
    assert bot.telegram.texts(CHAT) == [WELCOME, TEXT_ONLY]


async def test_group_chats_are_not_conversations(bot: Bot) -> None:
    response = await bot.write("/start demo", chat_type="group")
    assert response.status_code == 200
    assert bot.telegram.calls == []


@pytest.mark.parametrize("secret", [None, "guessed"])
async def test_only_telegram_knows_the_webhook_secret(bot: Bot, secret: str | None) -> None:
    response = await bot.write("/start demo", secret=secret)
    assert response.status_code == 401
    assert bot.telegram.calls == []


@pytest.mark.parametrize("bot_overrides", [{"telegram_bot_token": None}])
async def test_without_a_bot_there_is_no_webhook(bot: Bot) -> None:
    assert (await bot.write("/start demo")).status_code == 404
