"""The Telegram webhook, through the whole service in process: the app and
its lifespan, the local queue, Postgres, the fake Rezerve, a scripted model,
and a fake Bot API."""

from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from assistant.agent.handoff import BACK, HANDED_OFF
from assistant.channels.telegram_webhook import NO_BUSINESS, TEXT_ONLY, WELCOME, chat_id_reply
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.main import create_app
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices
from tests.fakes.models import Script, calls, says
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
        chat: int = CHAT,
        chat_type: str = "private",
        secret: str | None = SECRET,
        update_id: int | None = None,
        reply_to: int | None = None,
    ) -> httpx.Response:
        message: dict[str, Any] = {
            "message_id": 1,
            "from": {"id": chat, "is_bot": False, "first_name": "Elif"},
            "chat": {"id": chat, "type": chat_type},
            "date": 1_790_000_000,
            "text": text,
        }
        if reply_to is not None:
            message["reply_to_message"] = {
                "message_id": reply_to,
                "chat": {"id": chat, "type": chat_type},
                "date": 1_790_000_000,
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


OWNER = 9090


async def handed_to_the_owner(bot: Bot, store: Store) -> int:
    """A customer handed off to the linked owner; returns the notice's message id."""
    await store.link_owner("demo", OWNER)
    bot.script.add(calls("handoff_to_owner", {"reason": "şikayet"}))
    await bot.write("/start demo")
    await bot.write("berbat bir hizmetti, şikayetçiyim")
    await bot.settle()
    assert bot.telegram.texts(CHAT)[-1] == HANDED_OFF
    return bot.telegram.last_id(OWNER)


async def test_the_owner_answers_the_customer_by_replying_to_the_notice(
    bot: Bot, store: Store
) -> None:
    notice = await handed_to_the_owner(bot, store)

    await bot.write("Çok üzgünüz, sizi hemen arıyorum.", chat=OWNER, reply_to=notice)
    await bot.settle()

    assert bot.telegram.texts(CHAT)[-1] == "İşletme: Çok üzgünüz, sizi hemen arıyorum."


async def test_the_customers_next_words_reach_the_owner_who_can_reply_to_them(
    bot: Bot, store: Store
) -> None:
    await handed_to_the_owner(bot, store)
    await bot.write("ne zaman arayacaksınız?")
    await bot.settle()
    assert bot.telegram.texts(OWNER)[-1] == "Müşteri: ne zaman arayacaksınız?"

    await bot.write("10 dakika içinde.", chat=OWNER, reply_to=bot.telegram.last_id(OWNER))
    await bot.settle()
    assert bot.telegram.texts(CHAT)[-1] == "İşletme: 10 dakika içinde."


async def test_bitir_hands_the_conversation_back_to_the_assistant(bot: Bot, store: Store) -> None:
    notice = await handed_to_the_owner(bot, store)

    await bot.write("/bitir", chat=OWNER, reply_to=notice)
    await bot.settle()

    assert bot.telegram.texts(CHAT)[-1] == BACK
    bot.script.add(says("Buyurun, nasıl yardımcı olabilirim?"))
    await bot.write("yarın yer var mı?")
    await bot.settle()
    assert bot.telegram.texts(CHAT)[-1] == "Buyurun, nasıl yardımcı olabilirim?"


async def test_a_reply_to_any_other_message_is_an_ordinary_message(bot: Bot) -> None:
    bot.script.add(says("Merhaba!"))
    await bot.write("/start demo")
    await bot.write("merhaba", reply_to=bot.telegram.last_id(CHAT))
    await bot.settle()
    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba!"]


async def test_id_tells_a_chat_its_own_id(bot: Bot) -> None:
    # How an owner finds the number `assistant owners link` needs.
    await bot.write("/id")
    assert bot.telegram.texts(CHAT) == [chat_id_reply(CHAT)]


async def test_an_owner_testing_as_their_own_customer_is_told_apart_by_reply(
    bot: Bot, store: Store
) -> None:
    # The demo's owner tries the bot from the chat that is linked as the
    # owner's: what they reply to decides which of the two is speaking.
    await store.link_owner("demo", CHAT)
    bot.script.add(calls("handoff_to_owner", {"reason": "şikayet"}))
    await bot.write("/start demo")
    await bot.write("şikayetçiyim")
    await bot.settle()
    notice = bot.telegram.last_id(CHAT)

    await bot.write("orada mısınız?")
    await bot.settle()
    assert bot.telegram.texts(CHAT)[-1] == "Müşteri: orada mısınız?"

    await bot.write("Buradayım.", reply_to=notice)
    await bot.settle()
    assert bot.telegram.texts(CHAT)[-1] == "İşletme: Buradayım."


async def test_a_message_whose_turn_was_not_queued_is_answered_when_telegram_resends_it(
    bot: Bot, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cloud Tasks can refuse for a moment: the webhook fails, Telegram sends
    # the update again, and the message stored the first time must still be
    # answered (spec §7.2).
    bot.script.add(says("Merhaba!"))
    await bot.write("/start demo")
    queue = cast(LocalTaskQueue, cast(AppServices, bot.app.state.services).queue)
    enqueue = queue.enqueue_turn

    async def unavailable(conversation_id: int, message_seq: int) -> None:
        monkeypatch.setattr(queue, "enqueue_turn", enqueue)
        raise RuntimeError("Cloud Tasks is unavailable")

    monkeypatch.setattr(queue, "enqueue_turn", unavailable)
    with pytest.raises(RuntimeError):
        await bot.write("merhaba", update_id=5000)
    await bot.write("merhaba", update_id=5000)
    await bot.settle()

    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba!"]


@pytest.mark.parametrize("change", ["unlinked", "linked to another chat"])
async def test_a_chat_no_longer_the_owners_cannot_answer_for_the_business(
    bot: Bot, store: Store, change: str
) -> None:
    notice = await handed_to_the_owner(bot, store)
    if change == "unlinked":
        await store.unlink_owner("demo")
    else:
        await store.link_owner("demo", 9191)

    await bot.write("Hâlâ buradayım.", chat=OWNER, reply_to=notice)
    await bot.settle()

    assert bot.telegram.texts(CHAT)[-1] == HANDED_OFF
    assert bot.telegram.texts(OWNER)[-1] == NO_BUSINESS
