"""The in-process queue for development, tests and evals: Cloud Tasks'
contract — named tasks, retries with backoff — without Cloud Tasks."""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from assistant.conversation.lease import LeaseBusy

log = logging.getLogger(__name__)

# Cloud Tasks remembers a task name for about an hour; this remembers the last
# thousand, which covers any redelivery a local run sees.
REMEMBERED_NAMES = 1000


class LocalTaskQueue:
    def __init__(
        self,
        handler: Callable[[int], Awaitable[None]],
        *,
        max_attempts: int = 6,
        backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        self._handler = handler
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._names: deque[str] = deque(maxlen=REMEMBERED_NAMES)
        self._tasks: set[asyncio.Task[None]] = set()

    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        name = f"turn-{conversation_id}-{message_seq}"
        if name in self._names:
            return
        self._names.append(name)
        task = asyncio.create_task(self._run(conversation_id), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, conversation_id: int) -> None:
        for attempt in range(1, self._max_attempts + 1):
            try:
                await self._handler(conversation_id)
                return
            except LeaseBusy:
                pass
            except Exception:
                log.exception("Turn %s failed, attempt %s", conversation_id, attempt)
            await asyncio.sleep(min(self._backoff * 2 ** (attempt - 1), self._max_backoff))
        log.error("Turn %s gave up after %s attempts", conversation_id, self._max_attempts)

    async def drain(self) -> None:
        """Wait until every task, including those queued meanwhile, is done."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
