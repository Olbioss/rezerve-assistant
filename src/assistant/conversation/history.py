"""A conversation's memory: Pydantic AI messages, stored as JSON and trimmed
so the model sees the recent exchanges without ever losing half of a tool
call — a tool's return without the call that asked for it is an error at
every provider."""

from collections.abc import Sequence
from typing import Any

from pydantic_ai import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

MAX_MESSAGES = 20


def dump(messages: Sequence[ModelMessage]) -> list[Any]:
    return ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")


def load(raw: Sequence[Any]) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(list(raw))


def _starts_exchange(message: ModelMessage) -> bool:
    """A customer's message that owes nothing to an earlier tool call."""
    if not isinstance(message, ModelRequest):
        return False
    parts = message.parts
    return any(isinstance(part, UserPromptPart) for part in parts) and not any(
        isinstance(part, ToolReturnPart | RetryPromptPart) for part in parts
    )


def trim(messages: Sequence[ModelMessage], limit: int = MAX_MESSAGES) -> list[ModelMessage]:
    """The last `limit` messages or so, starting at a customer's message.

    It starts at the first clean start inside the window; with none there (one
    long run of tool calls) it reaches back to the nearest one before, keeping
    more rather than cutting a call from its return.
    """
    if len(messages) <= limit:
        return list(messages)
    window_start = len(messages) - limit
    for start in range(window_start, len(messages)):
        if _starts_exchange(messages[start]):
            return list(messages[start:])
    for start in range(window_start - 1, -1, -1):
        if _starts_exchange(messages[start]):
            return list(messages[start:])
    return list(messages)


def close_open_calls(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """The history without a last response whose tool calls were never answered.

    That is a proposal the customer's answer has already been spent on — say
    the run resuming it failed. A new run cannot start after unanswered calls,
    so the proposal is dropped; the model will propose again if it still should.
    """
    last = messages[-1] if messages else None
    if isinstance(last, ModelResponse) and any(isinstance(p, ToolCallPart) for p in last.parts):
        return list(messages[:-1])
    return list(messages)
