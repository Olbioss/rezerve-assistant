"""Telegram's Bot API, stood in for over httpx: every call is kept, and
sendMessage answers the way Telegram does, unless told to fail."""

import json
from typing import Any

import httpx


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # (chat id, message id) of every message delivered, in order.
        self.delivered: list[tuple[int, int]] = []
        self._next_id = 100
        # An error Telegram returns to every call from now on, as (status, description).
        self.fail_with: tuple[int, str] | None = None
        # What getWebhookInfo answers; setWebhook sets its url.
        self.webhook: dict[str, Any] = {"url": "", "pending_update_count": 0}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload: dict[str, Any] = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if self.fail_with is not None:
            status, description = self.fail_with
            return httpx.Response(
                status, json={"ok": False, "error_code": status, "description": description}
            )
        if method == "setWebhook":
            self.webhook["url"] = payload["url"]
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "getWebhookInfo":
            return httpx.Response(200, json={"ok": True, "result": self.webhook})
        self._next_id += 1
        self.delivered.append((int(payload.get("chat_id", 0)), self._next_id))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_id}})

    def sent(self) -> list[dict[str, Any]]:
        """Every sendMessage, in order."""
        return [payload for method, payload in self.calls if method == "sendMessage"]

    def texts(self, chat_id: int) -> list[str]:
        return [message["text"] for message in self.sent() if message["chat_id"] == chat_id]

    def last_id(self, chat_id: int) -> int:
        """The message id Telegram gave the last message sent to this chat."""
        return [message_id for chat, message_id in self.delivered if chat == chat_id][-1]
