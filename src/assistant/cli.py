"""Admin commands.

`uv run assistant owners link <slug> <telegram_chat_id>`: a business owner
learns their chat's id by sending /id to the bot. Linking it sends that
business's handoffs to the owner (spec §7.6); a self-service pairing in the
Rezerve panel is v2.

`uv run assistant config check <file>`: the production config, before it is
uploaded (assistant.config_check).

`uv run assistant telegram webhook <file>`: Telegram told to post the bot's
messages to the service that config describes.
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

import httpx

from assistant import config_check
from assistant.channels.telegram import TelegramApi, TelegramError
from assistant.config import DatabaseSettings, from_file
from assistant.conversation.store import Store
from assistant.db.session import create_engine, session_factory


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="assistant")
    groups = parser.add_subparsers(dest="group", required=True)
    owners = groups.add_parser("owners", help="the business owners handoffs go to")
    actions = owners.add_subparsers(dest="action", required=True)
    link = actions.add_parser("link", help="send a business's handoffs to a Telegram chat")
    link.add_argument("slug")
    link.add_argument("chat_id", type=int)
    unlink = actions.add_parser("unlink", help="stop; handoffs give the business's phone again")
    unlink.add_argument("slug")
    config = groups.add_parser("config", help="the production config file")
    checks = config.add_subparsers(dest="action", required=True)
    check = checks.add_parser("check", help="find its problems, naming keys, never values")
    check.add_argument("path", type=Path)
    check.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="a value the deploy set up, such as PUBLIC_BASE_URL",
    )
    telegram = groups.add_parser("telegram", help="the Telegram bot")
    bot = telegram.add_subparsers(dest="action", required=True)
    webhook = bot.add_parser("webhook", help="send the bot's messages to the configured service")
    webhook.add_argument("path", type=Path)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> str:
    engine = create_engine(DatabaseSettings().database_url)
    try:
        store = Store(session_factory(engine))
        if args.action == "link":
            await store.link_owner(args.slug, args.chat_id)
            return f"{args.slug}: handoffs go to Telegram chat {args.chat_id}"
        await store.unlink_owner(args.slug)
        return f"{args.slug}: no owner chat; handoffs give the business's phone"
    finally:
        await engine.dispose()


async def set_webhook(path: Path, transport: httpx.AsyncBaseTransport | None) -> int:
    if found := config_check.problems(path, {}):
        print("\n".join(found))
        return 1
    settings = from_file(path)
    token, secret = settings.telegram_bot_token, settings.telegram_webhook_secret
    if token is None or secret is None:
        print("TELEGRAM_BOT_TOKEN: not in the file")
        return 1
    if not settings.public_base_url.startswith("https://"):
        print("PUBLIC_BASE_URL: Telegram calls only https URLs")
        return 1
    url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
    async with httpx.AsyncClient(transport=transport, timeout=15) as client:
        api = TelegramApi(token.get_secret_value(), client)
        try:
            await api.set_webhook(url, secret.get_secret_value())
            info = await api.webhook_info()
        except TelegramError as error:
            # Its message has Telegram's answer, never the URL with the token.
            print(error)
            return 1
    print(f"Webhook: {info.get('url')}")
    print(f"Pending updates: {info.get('pending_update_count', 0)}")
    print(f"Last error: {info.get('last_error_message') or 'none'}")
    return 0


def main(
    argv: Sequence[str] | None = None, *, telegram: httpx.AsyncBaseTransport | None = None
) -> int:
    """`telegram` stands in for Telegram's API in tests."""
    args = parse(argv)
    if args.group == "telegram":
        return asyncio.run(set_webhook(args.path, telegram))
    if args.group == "config":
        expected = dict(item.partition("=")[::2] for item in args.expect)
        found = config_check.problems(args.path, expected)
        print("\n".join(found) if found else "No problems.")
        return 1 if found else 0
    print(asyncio.run(run(args)))
    return 0
