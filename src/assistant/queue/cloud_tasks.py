"""Cloud Tasks (spec §4, §9). A turn is a named task that calls this service
back at /internal/turn/{conversation}, with an OIDC token Cloud Tasks mints
for the invoker service account and this service's URL as its audience.

Cloud Tasks refuses a task name it has seen in about the last hour, so a
redelivered webhook asks for its turn once, as it does on the local queue.
"""

import logging
from typing import Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2

log = logging.getLogger(__name__)

Client = tasks_v2.CloudTasksAsyncClient


class TaskCreator(Protocol):
    """The one Cloud Tasks call this service makes."""

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task: ...


class CloudTasksQueue:
    def __init__(
        self,
        client: TaskCreator,
        *,
        project: str,
        region: str,
        turns_queue: str,
        base_url: str,
        invoker: str,
    ) -> None:
        self._client = client
        self._project = project
        self._region = region
        self._turns = turns_queue
        self._base_url = base_url.rstrip("/")
        self._invoker = invoker

    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        await self._create(
            self._turns,
            f"turn-{conversation_id}-{message_seq}",
            f"/internal/turn/{conversation_id}",
        )

    async def _create(self, queue: str, name: str, path: str) -> None:
        parent = Client.queue_path(self._project, self._region, queue)
        task = tasks_v2.Task(
            name=Client.task_path(self._project, self._region, queue, name),
            http_request=tasks_v2.HttpRequest(
                url=f"{self._base_url}{path}",
                http_method=tasks_v2.HttpMethod.POST,
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._invoker, audience=self._base_url
                ),
            ),
        )
        try:
            await self._client.create_task(parent=parent, task=task)
        except AlreadyExists:
            log.info("Task %s is already queued", name)
