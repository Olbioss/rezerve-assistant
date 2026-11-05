"""What the HTTP layer reaches for: built once in the app's lifespan."""

from dataclasses import dataclass
from typing import cast

from fastapi import Request

from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.queue.base import TaskQueue
from assistant.ratelimit import SlidingWindow


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    store: Store
    queue: TaskQueue
    # The widget's posts per client address.
    post_limit: SlidingWindow


def app_services(request: Request) -> AppServices:
    return cast(AppServices, request.app.state.services)
