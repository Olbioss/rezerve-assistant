import asyncio

import pytest

from assistant.cli import main
from assistant.conversation.store import Store
from assistant.db.session import create_engine, session_factory

SLUG = "cli-test-salon"


def owner_of(database_url: str, slug: str) -> int | None:
    async def read() -> int | None:
        engine = create_engine(database_url)
        try:
            return await Store(session_factory(engine)).owner_chat(slug)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_an_owner_chat_is_linked_from_the_command_line(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Spec §7.6: `uv run assistant owners link <slug> <telegram_chat_id>`.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("CONFIG_FILE", raising=False)

    assert main(["owners", "link", SLUG, "777"]) == 0
    assert owner_of(database_url, SLUG) == 777
    assert main(["owners", "link", SLUG, "888"]) == 0
    assert owner_of(database_url, SLUG) == 888
    assert main(["owners", "unlink", SLUG]) == 0
    assert owner_of(database_url, SLUG) is None
    assert SLUG in capsys.readouterr().out
