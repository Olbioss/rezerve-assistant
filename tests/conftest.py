import os

import pytest
from alembic import command
from alembic.config import Config

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
