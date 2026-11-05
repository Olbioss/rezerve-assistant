"""A task queue that only keeps what it was asked: turns and reminders."""

from datetime import datetime


class RecordingQueue:
    def __init__(self) -> None:
        self.turns: list[tuple[int, int]] = []
        self.reminders: list[tuple[str, datetime, datetime]] = []

    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        self.turns.append((conversation_id, message_seq))

    async def enqueue_reminder(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
        self.reminders.append((booking_id, starts_at, at))
