"""What every channel shares: a message in, a message out, and how to send."""

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict

type ChannelName = Literal["web", "telegram", "whatsapp"]


class LinkAction(TypedDict):
    """A link the channel shows as a button — the kapora payment page."""

    type: Literal["link"]
    label: str
    url: str


type Action = LinkAction


@dataclass(frozen=True)
class InboundMessage:
    business_slug: str
    channel: ChannelName
    channel_user_id: str
    # Unique across the whole channel, so a redelivery is recognised.
    channel_message_id: str
    text: str


@dataclass(frozen=True)
class OutboundMessage:
    seq: int
    text: str
    actions: list[Action] = field(default_factory=list[Action])


class Channel(Protocol):
    name: ChannelName

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        """Deliver one message and return the id the channel gave it."""
        ...
