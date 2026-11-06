"""What the HTTP layer reaches for: built once in the app's lifespan."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from fastapi import Request

from assistant.channels.telegram import TelegramApi
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.queue.base import TaskQueue
from assistant.ratelimit import ForwardedLayout, SlidingWindow

# Checks a bearer token for an audience and returns its claims (spec §5).
type TokenVerifier = Callable[[str, str], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    store: Store
    queue: TaskQueue
    # The widget's posts per client address.
    post_limit: SlidingWindow
    # Logs how many X-Forwarded-For entries the instance's first post carried.
    forwarded: ForwardedLayout
    # Answers a conversation's unprocessed messages: what a turn task runs.
    process_turn: Callable[[int], Awaitable[None]]
    verify_token: TokenVerifier
    # None while no bot token is set: the Telegram webhook is off.
    telegram: TelegramApi | None
    # Sends a booking's reminder: what a reminder task runs.
    send_reminder: Callable[[str, datetime], Awaitable[None]]
    # Sends the spans finished so far; /internal calls run it before answering.
    flush_traces: Callable[[], Awaitable[None]]


def app_services(request: Request) -> AppServices:
    return cast(AppServices, request.app.state.services)
