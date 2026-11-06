import asyncio
import json
from pathlib import Path

import pytest

from assistant.cli import main
from assistant.conversation.store import Store
from assistant.db.session import create_engine, session_factory

SLUG = "cli-test-salon"
EXAMPLE = Path(__file__).parents[2] / "deploy" / "config.example.json"


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


def test_with_the_production_file_its_database_alone_is_written(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Go-live links the owner on the production database. A DATABASE_URL in
    # the shell, or in a .env beside the file, must not send the write
    # elsewhere while the command reports success.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody:nothing@localhost:1/elsewhere")
    monkeypatch.delenv("CONFIG_FILE", raising=False)
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    values = {key: value.replace("REPLACE_ME", "x1") for key, value in example.items()} | {
        "DATABASE_URL": database_url,
        "PUBLIC_BASE_URL": "https://assistant-1.europe-west1.run.app",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")

    assert main(["owners", "link", SLUG, "999", "--config", str(path)]) == 0
    assert owner_of(database_url, SLUG) == 999
    assert main(["owners", "unlink", SLUG, "--config", str(path)]) == 0
    assert owner_of(database_url, SLUG) is None
    assert f"{SLUG}: handoffs go to Telegram chat 999" in capsys.readouterr().out


def test_a_production_file_with_problems_writes_nothing(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    path = tmp_path / "config.json"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["owners", "link", SLUG, "555", "--config", str(path)]) == 1
    assert "DATABASE_URL: still the example's placeholder" in capsys.readouterr().out
    assert owner_of(database_url, SLUG) != 555
