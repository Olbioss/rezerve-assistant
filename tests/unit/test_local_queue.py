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
