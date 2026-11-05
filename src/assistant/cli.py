"""Admin commands: `uv run assistant owners link <slug> <telegram_chat_id>`.

A business owner learns their chat's id by sending /id to the bot. Linking it
sends that business's handoffs to the owner (spec §7.6); a self-service
pairing in the Rezerve panel is v2.
"""

import argparse
import asyncio
from collections.abc import Sequence

from assistant.config import DatabaseSettings
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


def main(argv: Sequence[str] | None = None) -> int:
    print(asyncio.run(run(parse(argv))))
    return 0
