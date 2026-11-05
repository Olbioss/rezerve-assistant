"""Channels that keep what they were asked to send."""

from assistant.channels.base import ChannelName, OutboundMessage


class TelegramOutbox:
    """The Telegram channel, kept: who each message went to, and what it said."""

    name: ChannelName = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        self.sent.append((channel_user_id, message.text))
        return f"telegram:{len(self.sent)}"
