"""The widget's endpoints, through the whole service in process: the app and
its lifespan, the local queue, Postgres, the fake Rezerve, a scripted model."""

from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.main import create_app
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices
from tests.fakes.models import Script, says
from tests.fakes.rezerve import FakeRezerve

ORIGIN = "http://localhost:3000"


class Widget:
    """Talks to the service the way components/assistant/chat-widget.tsx does."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient) -> None:
        self.app = app
        self.client = client
        self.visitor_id = str(uuid4())

    async def post(
        self, text: str, message_id: str | None = None, slug: str = "demo"
    ) -> httpx.Response:
        body = {
            "visitor_id": self.visitor_id,
            "client_message_id": message_id or str(uuid4()),
            "text": text,
        }
        return await self.client.post(f"/web/{slug}/messages", json=body)

    async def poll(self, after: int = 0, slug: str = "demo") -> dict[str, Any]:
        params = {"visitor_id": self.visitor_id, "after": str(after)}
        response = await self.client.get(f"/web/{slug}/messages", params=params)
        assert response.status_code == 200
        return cast(dict[str, Any], response.json())

    async def settle(self) -> None:
        services = cast(AppServices, self.app.state.services)
        await cast(LocalTaskQueue, services.queue).drain()


@pytest.fixture
def script() -> Script:
    return Script()


@pytest.fixture
async def widget(database_url: str, store: Store, script: Script) -> AsyncIterator[Widget]:
    # `store` empties the tables; the app opens its own engine on them.
    fake = FakeRezerve()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        allowed_web_origins=[ORIGIN],
    )
    app = create_app(settings, model=script.model, open_rezerve=lambda slug, ref: fake.session())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Widget(app, client)


async def test_a_message_is_answered_and_polled_back(widget: Widget, script: Script) -> None:
    script.add(says("Merhaba! Size nasıl yardımcı olabilirim?"))
    message_id = str(uuid4())
    response = await widget.post("merhaba", message_id)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}

    await widget.settle()
    page = await widget.poll()
    assert page["pending"] is False
    assert [(m["direction"], m["text"], m["client_message_id"]) for m in page["messages"]] == [
        ("in", "merhaba", message_id),
        ("out", "Merhaba! Size nasıl yardımcı olabilirim?", None),
    ]
    assert await widget.poll(after=page["messages"][-1]["seq"]) == {
        "messages": [],
        "pending": False,
    }


async def test_a_resent_message_is_answered_once(widget: Widget, script: Script) -> None:
    script.add(says("Merhaba!"))
    message_id = str(uuid4())
    assert (await widget.post("merhaba", message_id)).status_code == 202
    assert (await widget.post("merhaba", message_id)).status_code == 202
    await widget.settle()
    page = await widget.poll()
    assert [m["direction"] for m in page["messages"]] == ["in", "out"]
    assert script.done


async def test_a_visitor_sees_only_their_own_conversation(widget: Widget, script: Script) -> None:
    script.add(says("Merhaba!"))
    await widget.post("merhaba")
    await widget.settle()
    widget.visitor_id = str(uuid4())
    assert await widget.poll() == {"messages": [], "pending": False}


@pytest.mark.parametrize(
    ("slug", "body"),
    [
        ("demo", {"visitor_id": "not-a-uuid", "client_message_id": str(uuid4()), "text": "hi"}),
        ("demo", {"visitor_id": str(uuid4()), "client_message_id": str(uuid4()), "text": "   "}),
        (
            "demo",
            {"visitor_id": str(uuid4()), "client_message_id": str(uuid4()), "text": "a" * 1001},
        ),
        (
            "Demo_Salon",
            {"visitor_id": str(uuid4()), "client_message_id": str(uuid4()), "text": "hi"},
        ),
    ],
)
async def test_malformed_input_is_refused(widget: Widget, slug: str, body: dict[str, str]) -> None:
    response = await widget.client.post(f"/web/{slug}/messages", json=body)
    assert response.status_code == 422


async def test_only_rezerves_origin_may_call_from_a_browser(widget: Widget) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    allowed = await widget.client.options(
        "/web/demo/messages", headers={"Origin": ORIGIN, **preflight}
    )
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    refused = await widget.client.options(
        "/web/demo/messages", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in refused.headers


async def test_health(widget: Widget) -> None:
    assert (await widget.client.get("/health")).json() == {"status": "ok"}
