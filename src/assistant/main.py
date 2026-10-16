"""The service: `uvicorn assistant.main:create_app --factory`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic_ai.models import Model

from assistant.agent.agent import build_agent, build_model
from assistant.agent.approvals import ReplyClassifier
from assistant.channels import web
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.conversation.turn import OpenRezerve, TurnProcessor, TurnSettings
from assistant.db.session import create_engine, session_factory
from assistant.mcp import connect
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices


def create_app(
    settings: Settings | None = None,
    *,
    model: Model | None = None,
    open_rezerve: OpenRezerve | None = None,
) -> FastAPI:
    """Tests pass a model and a Rezerve of their own; production builds both
    from settings."""
    config = settings or Settings()  # pyright: ignore[reportCallIssue]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_engine(config.database_url)
        store = Store(session_factory(engine))
        chosen = model or build_model(config)
        processor = TurnProcessor(
            store=store,
            agent=build_agent(chosen),
            classifier=ReplyClassifier(chosen),
            open_rezerve=open_rezerve
            or partial(
                connect, config.rezerve_mcp_url, config.assistant_mcp_token.get_secret_value()
            ),
            channels={"web": web.WebChannel()},
            settings=TurnSettings.from_settings(config),
        )
        queue = LocalTaskQueue(processor.process)
        app.state.services = AppServices(settings=config, store=store, queue=queue)
        try:
            yield
        finally:
            await queue.aclose()
            await engine.dispose()

    app = FastAPI(title="Rezerve Assistant", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_web_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Visitor-Id"],
        # The widget polls every second; one preflight per ten minutes is plenty.
        max_age=600,
    )
    app.include_router(web.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
