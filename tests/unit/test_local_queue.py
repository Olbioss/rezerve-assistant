import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from assistant.conversation.lease import LeaseBusy
from assistant.queue.local import LocalTaskQueue


class Handler:
    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.calls: list[int] = []

    async def __call__(self, conversation_id: int) -> None:
        self.calls.append(conversation_id)
        if self.failures:
            raise self.failures.pop(0)


def queue(handler: Handler) -> LocalTaskQueue:
    return LocalTaskQueue(handler, max_attempts=3, backoff_seconds=0.01)


async def test_asking_twice_for_one_message_runs_once() -> None:
    handler = Handler()
    tasks = queue(handler)
    await tasks.enqueue_turn(1, message_seq=7)
    await tasks.enqueue_turn(1, message_seq=7)
    await tasks.enqueue_turn(1, message_seq=8)
    await tasks.drain()
    assert handler.calls == [1, 1]


async def test_a_busy_conversation_is_tried_again() -> None:
    handler = Handler(LeaseBusy(1))
    tasks = queue(handler)
    await tasks.enqueue_turn(1, message_seq=7)
    await tasks.drain()
    assert handler.calls == [1, 1]


async def test_a_failing_turn_is_retried_then_given_up(caplog: pytest.LogCaptureFixture) -> None:
    handler = Handler(*(RuntimeError("down") for _ in range(5)))
    tasks = queue(handler)
    await tasks.enqueue_turn(1, message_seq=7)
    await tasks.drain()
    assert handler.calls == [1, 1, 1]
    assert "gave up after 3 attempts" in caplog.text


async def test_a_reminder_fires_at_its_time_and_a_drain_does_not_wait_for_it() -> None:
    fired: list[tuple[str, datetime]] = []

    async def remind(booking_id: str, starts_at: datetime) -> None:
        fired.append((booking_id, starts_at))

    tasks = LocalTaskQueue(Handler(), remind=remind)
    starts_at = datetime.now(UTC) + timedelta(days=2)
    await tasks.enqueue_reminder("b-1", starts_at, datetime.now(UTC) + timedelta(milliseconds=50))
    await tasks.enqueue_reminder("b-1", starts_at, datetime.now(UTC) + timedelta(milliseconds=50))
    await tasks.enqueue_reminder("b-2", starts_at, datetime.now(UTC) + timedelta(days=1))

    await tasks.drain()  # turns only: the day-away reminder would hold it up
    await asyncio.sleep(0.2)

    assert fired == [("b-1", starts_at)]
    await tasks.aclose()
