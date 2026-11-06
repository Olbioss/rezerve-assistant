"""`assistant telegram webhook <file>`: Telegram told where to send the bot's
updates, and with which secret (spec §7.9), from the production config."""

import json
from pathlib import Path

import pytest

from assistant.cli import main
from tests.fakes.telegram import FakeTelegram

SERVICE = "https://assistant-123456789012.europe-west1.run.app"
TOKEN = "123456:bot-token"
SECRET = "webhook-secret"


def config(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "ASSISTANT_MCP_TOKEN": "t",
        "CUSTOMER_REF_SECRET": "s",
        "PUBLIC_BASE_URL": SERVICE,
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "TELEGRAM_WEBHOOK_SECRET": SECRET,
    } | overrides
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_telegram_sends_the_bots_messages_to_this_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    code = main(["telegram", "webhook", str(config(tmp_path))], telegram=telegram.transport)
    assert code == 0
    assert telegram.calls == [
        (
            "setWebhook",
            {
                "url": f"{SERVICE}/telegram/webhook",
                "secret_token": SECRET,
                # The webhook reads messages; edits, reactions and the rest
                # would be requests for nothing.
                "allowed_updates": ["message"],
            },
        ),
        ("getWebhookInfo", {}),
    ]
    out = capsys.readouterr().out
    assert out.splitlines() == [
        f"Webhook: {SERVICE}/telegram/webhook",
        "Pending updates: 0",
        "Last error: none",
    ]


def test_telegrams_last_error_is_shown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Telegram keeps the last failure delivering an update: a wrong secret
    # shows here as the webhook's 404.
    telegram = FakeTelegram()
    telegram.webhook |= {
        "pending_update_count": 3,
        "last_error_message": "Wrong response from the webhook: 404 Not Found",
    }
    main(["telegram", "webhook", str(config(tmp_path))], telegram=telegram.transport)
    assert capsys.readouterr().out.splitlines()[1:] == [
        "Pending updates: 3",
        "Last error: Wrong response from the webhook: 404 Not Found",
    ]


def test_a_refusal_is_shown_without_the_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    telegram.fail_with = (401, "Unauthorized")
    assert main(["telegram", "webhook", str(config(tmp_path))], telegram=telegram.transport) == 1
    out = capsys.readouterr().out
    assert out == "Telegram refused the call: 401 Unauthorized\n"


def test_a_config_with_problems_sets_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    path = config(tmp_path, PUBLIC_BASE_URL="http://localhost:8000")
    assert main(["telegram", "webhook", str(path)], telegram=telegram.transport) == 1
    assert telegram.calls == []
    # Telegram calls only https URLs; a local service is reached some other way.
    assert capsys.readouterr().out == "PUBLIC_BASE_URL: Telegram calls only https URLs\n"


def test_without_a_bot_there_is_nothing_to_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"ASSISTANT_MCP_TOKEN": "t", "CUSTOMER_REF_SECRET": "s"}), encoding="utf-8"
    )
    assert main(["telegram", "webhook", str(path)], telegram=telegram.transport) == 1
    assert telegram.calls == []
    assert capsys.readouterr().out == "TELEGRAM_BOT_TOKEN: not in the file\n"
