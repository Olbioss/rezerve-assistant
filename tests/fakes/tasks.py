"""Cloud Tasks, stood in for: create_task, refusing a name it has seen."""

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2


class FakeTasks:
    def __init__(self) -> None:
        self.created: list[tuple[str, tasks_v2.Task]] = []

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
        if any(seen.name == task.name for _, seen in self.created):
            raise AlreadyExists(f"{task.name} exists")
        self.created.append((parent, task))
        return task
