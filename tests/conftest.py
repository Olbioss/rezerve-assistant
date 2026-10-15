import os
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from assistant.conversation.store import Store
from assistant.db.session import create_engine, session_factory

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

# The Compose database (docker compose up -d db); CI points this at its own.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5433/assistant_test"
)


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database, migrated to head once per run."""
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[Store]:
    engine = create_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "TRUNCATE conversations, messages, pending_approvals, agent_runs "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield Store(session_factory(engine))
    await engine.dispose()
