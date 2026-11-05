from datetime import datetime
from typing import Protocol


class TaskQueue(Protocol):
    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        """Ask for the conversation's unprocessed messages to be answered.

        Named after the message that asked, so asking twice is asking once.
        """
        ...

    async def enqueue_reminder(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
        """Ask for the reminder of a booking starting at `starts_at` to be sent at `at`.

        Named after the booking and its start: a move is a new reminder.
        """
        ...


def reminder_name(booking_id: str, starts_at: datetime) -> str:
    return f"reminder-{booking_id}-{int(starts_at.timestamp())}"
