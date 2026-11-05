"""The service: `uvicorn assistant.main:create_app --factory`."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import tasks_v2
from pydantic_ai.models import Model

from assistant import events, internal, observability
from assistant.agent.agent import build_agent, build_model
from assistant.agent.approvals import ReplyClassifier
from assistant.channels import telegram_webhook, web
from assistant.channels.base import Channel, ChannelName
from assistant.channels.telegram import TelegramApi, TelegramChannel
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.conversation.turn import TurnProcessor, TurnSettings
from assistant.db.session import create_engine, session_factory
from assistant.mcp import OpenRezerve, connect
from assistant.queue.base import TaskQueue
from assistant.queue.cloud_tasks import CloudTasksQueue, TaskCreator
from assistant.queue.local import LocalTaskQueue
from assistant.ratelimit import SlidingWindow
from assistant.reminders import Reminders
from assistant.services import AppServices, TokenVerifier


def create_app(
    settings: Settings | None = None,
    *,
    model: Model | None = None,
    open_rezerve: OpenRezerve | None = None,
    tasks_client: TaskCreator | None = None,
    verify_token: TokenVerifier | None = None,
    telegram_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Tests pass a model, a Rezerve, Cloud Tasks, a token check and Telegram's
    transport of their own; production builds each from settings."""
    config = settings or Settings()  # pyright: ignore[reportCallIssue]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            engine = create_engine(config.database_url)
            stack.push_async_callback(engine.dispose)
            store = Store(session_factory(engine))
            chosen = model or build_model(config)
            channels: dict[ChannelName, Channel] = {"web": web.WebChannel()}
            telegram: TelegramApi | None = None
            if config.telegram_bot_token is not None:
                client = await stack.enter_async_context(
                    httpx.AsyncClient(transport=telegram_transport, timeout=15)
                )
                telegram = TelegramApi(config.telegram_bot_token.get_secret_value(), client)
                channels["telegram"] = TelegramChannel(telegram)
            open_session: OpenRezerve = open_rezerve or partial(
                connect, config.rezerve_mcp_url, config.assistant_mcp_token.get_secret_value()
            )
            reminders = Reminders(store, open_session, channels.get("telegram"))
            queue: TaskQueue
            if config.queue_backend == "cloud_tasks":
                if config.gcp_project is None or config.tasks_invoker_sa is None:
                    raise RuntimeError("Settings let cloud_tasks through without its project")
                client = tasks_client or await stack.enter_async_context(
                    tasks_v2.CloudTasksAsyncClient()
                )
                queue = CloudTasksQueue(
                    client,
                    project=config.gcp_project,
                    region=config.gcp_region,
                    turns_queue=config.tasks_queue_turns,
                    reminders_queue=config.tasks_queue_reminders,
                    base_url=config.public_base_url,
                    invoker=config.tasks_invoker_sa,
                )
            else:
                # The processor needs the queue for reminders, and the local
                # queue needs the processor for turns: bound on first use.
                async def answer(conversation_id: int) -> None:
                    await processor.process(conversation_id)

                local = LocalTaskQueue(answer, remind=reminders.send)
                stack.push_async_callback(local.aclose)
                queue = local
            processor = TurnProcessor(
                store=store,
                agent=build_agent(chosen),
                classifier=ReplyClassifier(chosen),
                open_rezerve=open_session,
                channels=channels,
                settings=TurnSettings.from_settings(config),
                queue=queue,
            )
            app.state.services = AppServices(
                settings=config,
                store=store,
                queue=queue,
                post_limit=SlidingWindow(
                    limit=config.ip_message_limit, seconds=config.sender_window_seconds
                ),
                process_turn=processor.process,
                verify_token=verify_token or internal.verify_google_token,
                telegram=telegram,
                send_reminder=reminders.send,
            )
            yield

    app = FastAPI(title="Rezerve Assistant", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_web_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Visitor-Id"],
        # The widget polls every second; one preflight per ten minutes is plenty.
        max_age=600,
    )
    observability.configure(config, app)
    app.include_router(web.router)
    app.include_router(telegram_webhook.router)
    app.include_router(events.router)
    app.include_router(internal.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
