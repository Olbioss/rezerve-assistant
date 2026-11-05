"""Telegram's webhook (spec §7.2, §7.9): it checks the secret Telegram sends
with every update, stores the customer's message once, and asks for a turn.

The deep link t.me/<bot>?start=<slug> on a business's booking page arrives as
"/start <slug>" and binds the chat to that business; a later one switches it.
"""

import hmac
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from assistant.channels.base import InboundMessage
from assistant.identity import customer_ref
from assistant.services import AppServices, app_services

WELCOME = (
    "Merhaba! Randevu almak, randevunuzu değiştirmek ya da sorularınız için bana yazabilirsiniz."
)
NO_BUSINESS = "Merhaba! Lütfen işletmenin randevu sayfasındaki Telegram bağlantısından başlayın."
TEXT_ONLY = "Şimdilik yalnızca yazılı mesajları anlayabiliyorum."

START = re.compile(r"^/start(?:@\w+)?(?:\s+(?P<payload>\S+))?$")
# Rezerve's own slug rule (lib/slug.ts).
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Telegram allows 4096 characters; a customer needs far fewer, and the model
# reads every one.
MAX_TEXT = 1000

type Services = Annotated[AppServices, Depends(app_services)]
type SecretHeader = Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")]

router = APIRouter(prefix="/telegram", tags=["telegram"])


class Chat(BaseModel):
    id: int
    type: str


class Message(BaseModel):
    message_id: int
    chat: Chat
    text: str | None = None


class Update(BaseModel):
    update_id: int
    message: Message | None = None


@router.post("/webhook")
async def webhook(
    update: Update, services: Services, secret: SecretHeader = None
) -> dict[str, bool]:
    telegram = services.telegram
    expected = services.settings.telegram_webhook_secret
    if telegram is None or expected is None:
        raise HTTPException(status_code=404)
    if secret is None or not hmac.compare_digest(
        secret.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status_code=401, detail="unauthorized")
    message = update.message
    # Edits, channel posts and group chats are no conversation with a customer.
    if message is None or message.chat.type != "private":
        return {"ok": True}
    chat_id = message.chat.id
    if message.text is None:
        await telegram.send_message(chat_id, TEXT_ONLY)
        return {"ok": True}
    now = datetime.now(UTC)
    if start := START.match(message.text.strip()):
        slug = start["payload"]
        if slug is None or not SLUG.match(slug):
            await telegram.send_message(chat_id, NO_BUSINESS)
        else:
            await services.store.bind_telegram_chat(chat_id, slug, now)
            await telegram.send_message(chat_id, WELCOME)
        return {"ok": True}
    slug = await services.store.telegram_business(chat_id)
    if slug is None:
        await telegram.send_message(chat_id, NO_BUSINESS)
        return {"ok": True}
    inbound = InboundMessage(
        business_slug=slug,
        channel="telegram",
        channel_user_id=str(chat_id),
        channel_message_id=str(update.update_id),
        text=message.text[:MAX_TEXT],
    )
    secret_key = services.settings.customer_ref_secret.get_secret_value()
    recorded = await services.store.record_inbound(
        inbound, customer_ref(secret_key, "telegram", str(chat_id)), now
    )
    if recorded.seq is not None:
        await services.queue.enqueue_turn(recorded.conversation_id, recorded.seq)
    return {"ok": True}
