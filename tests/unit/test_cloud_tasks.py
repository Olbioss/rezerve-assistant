import json
from datetime import UTC, datetime

from google.cloud import tasks_v2

from assistant.queue.cloud_tasks import CloudTasksQueue
from tests.fakes.tasks import FakeTasks

INVOKER = "assistant-tasks@rezerve-assistant.iam.gserviceaccount.com"


def tasks_queue(fake: FakeTasks) -> CloudTasksQueue:
    return CloudTasksQueue(
        fake,
        project="rezerve-assistant",
        region="europe-west1",
        turns_queue="turns",
        reminders_queue="reminders",
        base_url="https://assistant.example/",
        invoker=INVOKER,
    )


async def test_a_turn_is_a_named_task_calling_the_service_back_with_a_token_for_it() -> None:
    fake = FakeTasks()
    await tasks_queue(fake).enqueue_turn(7, 42)

    [(parent, task)] = fake.created
    assert parent == "projects/rezerve-assistant/locations/europe-west1/queues/turns"
    assert task.name == f"{parent}/tasks/turn-7-42"
    assert task.http_request.url == "https://assistant.example/internal/turn/7"
    assert task.http_request.http_method == tasks_v2.HttpMethod.POST
    assert task.http_request.oidc_token.service_account_email == INVOKER
    assert task.http_request.oidc_token.audience == "https://assistant.example"


async def test_asking_twice_for_one_turn_queues_it_once() -> None:
    # A redelivered webhook asks again under the same name; Cloud Tasks
    # refuses it, and that is not an error.
    fake = FakeTasks()
    queue = tasks_queue(fake)
    await queue.enqueue_turn(7, 42)
    await queue.enqueue_turn(7, 42)
    assert len(fake.created) == 1


async def test_a_reminder_is_a_task_for_its_time_on_the_reminders_queue() -> None:
    fake = FakeTasks()
    starts_at = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)
    at = datetime(2026, 11, 11, 12, 30, tzinfo=UTC)
    await tasks_queue(fake).enqueue_reminder("b-1", starts_at, at)

    [(parent, task)] = fake.created
    assert parent.endswith("/queues/reminders")
    assert task.name.endswith(f"/tasks/reminder-b-1-{int(starts_at.timestamp())}")
    assert task.schedule_time == at
    assert task.http_request.url == "https://assistant.example/internal/reminder"
    assert json.loads(task.http_request.body) == {
        "booking_id": "b-1",
        "starts_at": "2026-11-12T12:30:00+00:00",
    }
    assert task.http_request.headers["Content-Type"] == "application/json"
