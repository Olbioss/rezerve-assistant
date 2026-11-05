"""What the free tiers can bear (spec §7.5), checked before any model call.

The counts come from what the service already keeps: `agent_runs` holds every
turn's model requests and tokens, `messages` everything each sender wrote.
None switches a limit off.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from assistant.conversation.store import ConversationRecord, Store

type Spent = Literal["sender_rate", "daily_requests", "conversation_tokens"]

DAY = timedelta(days=1)

# Said once to a sender over the limit; after that, silence until they slow down.
SLOW_DOWN = (
    "Çok hızlı yazıyorsunuz, biraz yavaşlayalım. Birkaç dakika sonra yeniden yazabilirsiniz."
)


@dataclass(frozen=True)
class Budget:
    # Model requests in the last 24 hours, across every conversation: the
    # free tier's daily quota belongs to the whole service.
    daily_requests: int | None = None
    # Tokens one conversation may use in the last 24 hours.
    conversation_daily_tokens: int | None = None
    # Messages one sender may write in `sender_window`, in any conversation.
    sender_messages: int | None = None
    sender_window: timedelta = timedelta(minutes=10)

    async def spent(
        self, store: Store, conversation: ConversationRecord, now: datetime
    ) -> Spent | None:
        """Which limit this turn would cross, or None."""
        if self.sender_messages is not None:
            written = await store.inbound_since(
                conversation.channel, conversation.channel_user_id, now - self.sender_window
            )
            if written > self.sender_messages:
                return "sender_rate"
        if (
            self.daily_requests is not None
            and await store.requests_since(now - DAY) >= self.daily_requests
        ):
            return "daily_requests"
        if self.conversation_daily_tokens is not None:
            used = await store.tokens_since(conversation.id, now - DAY)
            if used >= self.conversation_daily_tokens:
                return "conversation_tokens"
        return None
