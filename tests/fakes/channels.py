"""Channels that keep what they were asked to send."""

from assistant.channels.base import ChannelName, OutboundMessage


class TelegramOutbox:
    """The Telegram channel, kept: who each message went to, and what it said."""

    name: ChannelName = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        # Chats Telegram refuses for good: they blocked the bot, or never existed.
        self.refused: set[str] = set()

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        if channel_user_id in self.refused:
            return "telegram:undelivered:403"
        self.sent.append((channel_user_id, message.text))
        return f"telegram:{len(self.sent)}"
