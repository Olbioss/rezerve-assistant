"""Telegram (spec §7.9): one bot for every business.

The bot token is part of every Bot API URL, so no URL of this module's is
logged or traced, and no error message carries one.
"""

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from assistant.channels.base import ChannelName, LinkAction, OutboundMessage

log = logging.getLogger(__name__)

# httpx logs each request's URL at INFO, and Telegram's URLs hold the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.telegram.org"
# Answers that retrying will never change: the chat is gone or blocked us.
UNREACHABLE = frozenset({400, 403})
# Telegram refuses a text over 4096 characters, counting some characters
# twice; a longer one is cut to arrive at all.
MAX_TEXT = 4000


class TelegramError(Exception):
    def __init__(self, status: int, description: str) -> None:
        super().__init__(f"Telegram refused the call: {status} {description}")
        self.status = status


class TelegramApi:
    """The Bot API calls this service makes."""

    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._base = f"{API}/bot{token}"
        self._client = client

    async def send_message(
        self, chat_id: int, text: str, buttons: Sequence[LinkAction] = ()
    ) -> int:
        """Send `text`, with each link as an inline button; returns its message id."""
        if len(text) > MAX_TEXT:
            text = text[: MAX_TEXT - 1] + "…"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": link["label"], "url": link["url"]}] for link in buttons
                ]
            }
        return int((await self._call("sendMessage", payload))["message_id"])

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as error:
            # httpx's message would name the URL, and with it the token.
            raise TelegramError(0, type(error).__name__) from None
        body: dict[str, Any] = response.json()
        if not body.get("ok"):
            status = int(body.get("error_code") or response.status_code)
            raise TelegramError(status, str(body.get("description", "")))
        result: dict[str, Any] = body["result"]
        return result


class TelegramChannel:
    name: ChannelName = "telegram"

    def __init__(self, api: TelegramApi) -> None:
        self._api = api

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        try:
            sent = await self._api.send_message(int(channel_user_id), message.text, message.actions)
        except TelegramError as error:
            if error.status not in UNREACHABLE:
                raise
            # Marked sent all the same: the replies after it must not wait on it.
            log.warning("Telegram chat %s unreachable: %s", channel_user_id, error)
            return f"telegram:undelivered:{error.status}"
        return f"telegram:{sent}"
