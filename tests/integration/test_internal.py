"""Cloud Tasks' side of the service, in process: a widget post queues a named
task, and that task's call back to /internal/turn answers the turn, but only
with a token Google signed for the invoker account (spec §5)."""

from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.internal import InvalidToken
from assistant.main import create_app
from tests.fakes.models import Script, says
from tests.fakes.rezerve import FakeRezerve
from tests.fakes.tasks import FakeTasks

BASE = "https://assistant.example"
INVOKER = "assistant-tasks@rezerve-assistant.iam.gserviceaccount.com"
TOKENS = {"invoker-token": INVOKER, "stranger-token": "someone@else.iam.gserviceaccount.com"}


async def verify(token: str, audience: str) -> Mapping[str, Any]:
    """Google's check, stood in for: one token per account the tests need."""
    if audience != BASE or token not in TOKENS:
        raise InvalidToken("Could not verify token")
    return {"email": TOKENS[token], "email_verified": True, "aud": audience}


class Flushes:
    """Stands in for sending the finished spans: counts each time it is asked."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


class Service:
    def __init__(
        self, client: httpx.AsyncClient, tasks: FakeTasks, script: Script, flushes: Flushes
    ) -> None:
        self.client = client
        self.tasks = tasks
        self.script = script
        self.flushes = flushes
        self.visitor_id = str(uuid4())

    async def post(self, text: str) -> None:
        body = {"visitor_id": self.visitor_id, "client_message_id": str(uuid4()), "text": text}
        response = await self.client.post("/web/demo/messages", json=body)
        assert response.status_code == 202

    def last_task_path(self) -> str:
        """Where the last task calls back, as a path on this service."""
        _, task = self.tasks.created[-1]
        return task.http_request.url.removeprefix(BASE)

    async def call_back(self, path: str, token: str | None = "invoker-token") -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await self.client.post(path, headers=headers)

    async def replies(self) -> list[str]:
        response = await self.client.get(
            "/web/demo/messages", headers={"X-Visitor-Id": self.visitor_id}
        )
        return [m["text"] for m in response.json()["messages"] if m["direction"] == "out"]


@pytest.fixture
async def service(database_url: str, store: Store) -> AsyncIterator[Service]:
    fake = FakeRezerve()
    tasks = FakeTasks()
    script = Script()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        public_base_url=BASE,
        queue_backend="cloud_tasks",
        gcp_project="rezerve-assistant",
        tasks_invoker_sa=INVOKER,
    )
    flushes = Flushes()
    app = create_app(
        settings,
        model=script.model,
        open_rezerve=lambda slug, ref: fake.session(),
        tasks_client=tasks,
        verify_token=verify,
        flush_traces=flushes,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Service(client, tasks, script, flushes)


async def test_a_message_queues_a_task_whose_call_back_answers_it(service: Service) -> None:
    service.script.add(says("Merhaba! Size nasıl yardımcı olabilirim?"))
    await service.post("merhaba")
    path = service.last_task_path()
    assert path.startswith("/internal/turn/")
    assert await service.replies() == []

    response = await service.call_back(path)

    assert response.status_code == 204
    assert await service.replies() == ["Merhaba! Size nasıl yardımcı olabilirim?"]


async def test_a_call_back_sends_its_traces_before_it_answers(service: Service) -> None:
    # Cloud Run gives an instance CPU only while it answers a request: spans
    # left to the exporter's own timer would wait for the next request, or go
    # with the instance.
    service.script.add(says("Merhaba!"))
    await service.post("merhaba")
    assert (await service.call_back(service.last_task_path())).status_code == 204
    assert service.flushes.count == 1


@pytest.mark.parametrize(
    ("authorization", "status"),
    [(None, 401), ("forged", 401), ("stranger-token", 403)],
)
async def test_only_the_invoker_account_may_start_a_turn(
    service: Service, authorization: str | None, status: int
) -> None:
    await service.post("merhaba")
    response = await service.call_back(service.last_task_path(), token=authorization)
    assert response.status_code == status
    assert await service.replies() == []


async def test_a_turn_another_handler_holds_is_tried_again_later(
    service: Service, store: Store
) -> None:
    await service.post("merhaba")
    path = service.last_task_path()
    conversation_id = int(path.rsplit("/", 1)[1])
    assert await store.claim_lease(conversation_id, "another-handler", timedelta(minutes=1))

    response = await service.call_back(path)

    # 429: Cloud Tasks retries it with backoff (spec §7.2).
    assert response.status_code == 429
    assert service.flushes.count == 1


async def test_a_reminder_task_calls_back_with_the_invokers_token(service: Service) -> None:
    body = {"booking_id": "no-such-booking", "starts_at": "2026-11-12T12:30:00+00:00"}
    refused = await service.client.post("/internal/reminder", json=body)
    assert refused.status_code == 401
    response = await service.client.post(
        "/internal/reminder", json=body, headers={"Authorization": "Bearer invoker-token"}
    )
    assert response.status_code == 204
    # The refused call never reached the endpoint: only the reminder flushed.
    assert service.flushes.count == 1
