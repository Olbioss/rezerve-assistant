from typing import Protocol


class TaskQueue(Protocol):
    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        """Ask for the conversation's unprocessed messages to be answered.

        Named after the message that asked, so asking twice is asking once.
        """
        ...
