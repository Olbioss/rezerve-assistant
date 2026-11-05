"""What the free tiers can bear (spec §7.5), checked before any model call.

The counts come from what the service already keeps: `agent_runs` holds every
turn's model requests and tokens. None switches a limit off.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from assistant.conversation.store import Store

type Spent = Literal["daily_requests", "conversation_tokens"]

DAY = timedelta(days=1)


@dataclass(frozen=True)
class Budget:
    # Model requests in the last 24 hours, across every conversation: the
    # free tier's daily quota belongs to the whole service.
    daily_requests: int | None = None
    # Tokens one conversation may use in the last 24 hours.
    conversation_daily_tokens: int | None = None

    async def spent(self, store: Store, conversation_id: int, now: datetime) -> Spent | None:
        """Which limit this turn would cross, or None."""
        if (
            self.daily_requests is not None
            and await store.requests_since(now - DAY) >= self.daily_requests
        ):
            return "daily_requests"
        if self.conversation_daily_tokens is not None:
            used = await store.tokens_since(conversation_id, now - DAY)
            if used >= self.conversation_daily_tokens:
                return "conversation_tokens"
        return None
