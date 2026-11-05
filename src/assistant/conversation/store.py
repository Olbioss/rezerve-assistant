"""Everything the service keeps about a conversation, one short transaction
at a time. No transaction is ever held open across a model call."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, cast

import sqlalchemy as sa
from pydantic_ai import ModelMessage
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant.channels.base import Action, ChannelName, InboundMessage, OutboundMessage
from assistant.conversation import history
from assistant.db.models import (
    AgentRun,
    BusinessConfig,
    Conversation,
    Message,
    OwnerRelay,
    PendingApproval,
    TelegramChat,
)

# A conversation idle this long is closed; the next message opens a new one.
IDLE_CLOSE = timedelta(hours=24)
# KVKK: transcripts idle this long are deleted when a new conversation opens.
RETENTION = timedelta(days=90)
PAGE_SIZE = 50

type Decision = Literal["approved", "denied", "expired"]
type Outcome = Literal["reply", "confirmation", "handoff", "degraded", "error"]
type State = Literal["active", "human"]


@dataclass(frozen=True)
class Recorded:
    conversation_id: int
    # None when this message was a redelivery, stored the first time.
    seq: int | None


@dataclass(frozen=True)
class ConversationRecord:
    id: int
    business_slug: str
    channel: ChannelName
    channel_user_id: str
    customer_ref: str
    state: str
    history: list[ModelMessage]
    # When the owner took it over; None unless the state is 'human'.
    handed_off_at: datetime | None = None


@dataclass(frozen=True)
class StoredInbound:
    seq: int
    text: str


@dataclass(frozen=True)
class OpenApproval:
    id: int
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    other_call_ids: list[str]
    rendered_text: str
    history: list[ModelMessage]
    expires_at: datetime
    # The message that showed the confirmation: only later ones can answer it.
    confirmation_seq: int | None = None


@dataclass(frozen=True)
class NewApproval:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    other_call_ids: list[str]
    rendered_text: str
    expires_at: datetime


@dataclass(frozen=True)
class Reply:
    text: str
    actions: list[Action] = field(default_factory=list[Action])


@dataclass(frozen=True)
class RunRecord:
    started_at: datetime
    duration_ms: int
    model_used: str | None
    models_tried: list[str]
    requests: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    outcome: Outcome


@dataclass(frozen=True)
class TurnResult:
    """Everything one turn changes, written in a single transaction."""

    processed: list[int]
    replies: list[Reply]
    # None leaves the stored history as it was.
    history: list[ModelMessage] | None = None
    run: RunRecord | None = None
    resolved: tuple[int, Decision] | None = None
    approval: NewApproval | None = None
    # Messages for the business owner's Telegram chat (spec §7.6).
    owner_notes: list[str] = field(default_factory=list[str])
    # The conversation's new state: 'human' hands it to the owner, 'active' back.
    state: State | None = None


@dataclass(frozen=True)
class PageMessage:
    seq: int
    direction: Literal["in", "out"]
    text: str
    actions: list[Action]
    client_message_id: str | None


@dataclass(frozen=True)
class MessagesPage:
    messages: list[PageMessage]
    # True while something is still to come: a message not yet answered.
    pending: bool


class Store:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record_inbound(
        self, message: InboundMessage, customer_ref: str, now: datetime
    ) -> Recorded:
        async with self._sessions.begin() as session:
            conversation_id = await self._open_conversation(session, message, customer_ref, now)
            inserted = await session.execute(
                pg_insert(Message)
                .values(
                    conversation_id=conversation_id,
                    channel=message.channel,
                    direction="in",
                    channel_message_id=message.channel_message_id,
                    text=message.text,
                )
                .on_conflict_do_nothing(
                    index_elements=["channel", "channel_message_id"],
                    index_where=sa.text("direction = 'in'"),
                )
                .returning(Message.seq)
            )
            seq = inserted.scalar_one_or_none()
            if seq is not None:
                await session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(last_message_at=now)
                )
            return Recorded(conversation_id=conversation_id, seq=seq)

    async def _open_conversation(
        self, session: AsyncSession, message: InboundMessage, customer_ref: str, now: datetime
    ) -> int:
        same_customer = (
            Conversation.business_slug == message.business_slug,
            Conversation.channel == message.channel,
            Conversation.channel_user_id == message.channel_user_id,
            Conversation.state != "closed",
        )
        current = (
            await session.execute(
                sa.select(Conversation.id, Conversation.last_message_at)
                .where(*same_customer)
                .with_for_update()
            )
        ).one_or_none()
        if current is not None:
            if current.last_message_at > now - IDLE_CLOSE:
                return current.id
            await session.execute(
                sa.update(Conversation)
                .where(Conversation.id == current.id)
                .values(state="closed", locked_until=None, lease_owner=None)
            )
        await session.execute(
            sa.delete(Conversation).where(Conversation.last_message_at < now - RETENTION)
        )
        created = await session.execute(
            pg_insert(Conversation)
            .values(
                business_slug=message.business_slug,
                channel=message.channel,
                channel_user_id=message.channel_user_id,
                customer_ref=customer_ref,
                last_message_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["business_slug", "channel", "channel_user_id"],
                index_where=sa.text("state <> 'closed'"),
            )
            .returning(Conversation.id)
        )
        conversation_id = created.scalar_one_or_none()
        if conversation_id is not None:
            return conversation_id
        # A concurrent request opened it a moment ago.
        return (
            await session.execute(sa.select(Conversation.id).where(*same_customer))
        ).scalar_one()

    async def conversation(self, conversation_id: int) -> ConversationRecord:
        async with self._sessions() as session:
            row = await session.get_one(Conversation, conversation_id)
            return ConversationRecord(
                id=row.id,
                business_slug=row.business_slug,
                channel=cast(ChannelName, row.channel),
                channel_user_id=row.channel_user_id,
                customer_ref=row.customer_ref,
                state=row.state,
                history=history.load(row.history),
                handed_off_at=row.handed_off_at,
            )

    async def claim_lease(self, conversation_id: int, owner: str, ttl: timedelta) -> bool:
        async with self._sessions.begin() as session:
            claimed = await session.execute(
                sa.update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    sa.or_(
                        Conversation.locked_until.is_(None),
                        Conversation.locked_until < sa.func.now(),
                    ),
                )
                .values(locked_until=sa.func.now() + ttl, lease_owner=owner)
                .returning(Conversation.id)
            )
            return claimed.scalar_one_or_none() is not None

    async def renew_lease(self, conversation_id: int, owner: str, ttl: timedelta) -> bool:
        """Extend a lease this owner still holds; False once it has lapsed."""
        async with self._sessions.begin() as session:
            renewed = await session.execute(
                sa.update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.lease_owner == owner,
                    Conversation.locked_until > sa.func.now(),
                )
                .values(locked_until=sa.func.now() + ttl)
                .returning(Conversation.id)
            )
            return renewed.scalar_one_or_none() is not None

    async def release_lease(self, conversation_id: int, owner: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                sa.update(Conversation)
                .where(Conversation.id == conversation_id, Conversation.lease_owner == owner)
                .values(locked_until=None, lease_owner=None)
            )

    async def unprocessed_inbound(self, conversation_id: int) -> list[StoredInbound]:
        async with self._sessions() as session:
            rows = await session.execute(
                sa.select(Message.seq, Message.text)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.direction == "in",
                    Message.processed_at.is_(None),
                )
                .order_by(Message.seq)
            )
            return [StoredInbound(seq=row.seq, text=row.text) for row in rows]

    async def open_approval(self, conversation_id: int) -> OpenApproval | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    sa.select(PendingApproval).where(
                        PendingApproval.conversation_id == conversation_id,
                        PendingApproval.resolved_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return OpenApproval(
                id=row.id,
                tool_call_id=row.tool_call_id,
                tool_name=row.tool_name,
                args=row.args,
                other_call_ids=list(row.other_call_ids),
                rendered_text=row.rendered_text,
                history=history.load(row.history),
                expires_at=row.expires_at,
                confirmation_seq=row.confirmation_seq,
            )

    async def commit_turn(self, conversation_id: int, result: TurnResult, now: datetime) -> None:
        async with self._sessions.begin() as session:
            if result.resolved is not None:
                approval_id, decision = result.resolved
                await session.execute(
                    sa.update(PendingApproval)
                    .where(PendingApproval.id == approval_id)
                    .values(resolved_at=now, decision=decision)
                )
            if result.history is not None:
                await session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(history=history.dump(result.history))
                )
            if result.state is not None:
                await session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(
                        state=result.state,
                        handed_off_at=now if result.state == "human" else None,
                    )
                )
            channel = (
                await session.execute(
                    sa.select(Conversation.channel).where(Conversation.id == conversation_id)
                )
            ).scalar_one()
            replies = [
                Message(
                    conversation_id=conversation_id,
                    channel=channel,
                    direction="out",
                    text=reply.text,
                    actions=list(reply.actions) or None,
                )
                for reply in result.replies
            ]
            session.add_all(replies)
            session.add_all(
                Message(
                    conversation_id=conversation_id,
                    channel="telegram",
                    direction="owner",
                    text=note,
                )
                for note in result.owner_notes
            )
            await session.flush()
            if result.approval is not None:
                approval = result.approval
                session.add(
                    PendingApproval(
                        conversation_id=conversation_id,
                        tool_call_id=approval.tool_call_id,
                        tool_name=approval.tool_name,
                        args=approval.args,
                        other_call_ids=approval.other_call_ids,
                        rendered_text=approval.rendered_text,
                        # The confirmation is this turn's last reply; a receipt
                        # for an earlier write may come before it.
                        confirmation_seq=replies[-1].seq if replies else None,
                        history=history.dump(result.history or []),
                        expires_at=approval.expires_at,
                    )
                )
            if result.run is not None:
                run = result.run
                session.add(
                    AgentRun(
                        conversation_id=conversation_id,
                        started_at=run.started_at,
                        duration_ms=run.duration_ms,
                        model_used=run.model_used,
                        models_tried=run.models_tried,
                        requests=run.requests,
                        input_tokens=run.input_tokens,
                        output_tokens=run.output_tokens,
                        tool_calls=run.tool_calls,
                        tool_errors=run.tool_errors,
                        outcome=run.outcome,
                    )
                )
            if result.processed:
                await session.execute(
                    sa.update(Message)
                    .where(Message.seq.in_(result.processed))
                    .values(processed_at=now)
                )

    async def runs(self, conversation_id: int) -> list[RunRecord]:
        """What each of the conversation's turns cost, oldest first."""
        async with self._sessions() as session:
            rows = await session.execute(
                sa.select(AgentRun)
                .where(AgentRun.conversation_id == conversation_id)
                .order_by(AgentRun.id)
            )
            return [
                RunRecord(
                    started_at=row.started_at,
                    duration_ms=row.duration_ms,
                    model_used=row.model_used,
                    models_tried=list(row.models_tried),
                    requests=row.requests,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    tool_calls=row.tool_calls,
                    tool_errors=row.tool_errors,
                    outcome=cast(Outcome, row.outcome),
                )
                for row in rows.scalars()
            ]

    async def requests_since(self, since: datetime) -> int:
        """Model requests made since `since`, by every conversation."""
        async with self._sessions() as session:
            total = await session.execute(
                sa.select(sa.func.coalesce(sa.func.sum(AgentRun.requests), 0)).where(
                    AgentRun.started_at >= since
                )
            )
            return int(total.scalar_one())

    async def tokens_since(self, conversation_id: int, since: datetime) -> int:
        """Tokens one conversation's turns have used since `since`."""
        async with self._sessions() as session:
            total = await session.execute(
                sa.select(
                    sa.func.coalesce(sa.func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0)
                ).where(AgentRun.conversation_id == conversation_id, AgentRun.started_at >= since)
            )
            return int(total.scalar_one())

    async def inbound_since(
        self, channel: ChannelName, channel_user_id: str, since: datetime
    ) -> int:
        """Messages one sender has written since `since`, in any conversation."""
        async with self._sessions() as session:
            count = await session.execute(
                sa.select(sa.func.count())
                .select_from(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Conversation.channel == channel,
                    Conversation.channel_user_id == channel_user_id,
                    Message.direction == "in",
                    Message.created_at >= since,
                )
            )
            return count.scalar_one()

    async def said_since(self, conversation_id: int, text: str, since: datetime) -> bool:
        """Whether this conversation was told `text` since `since`."""
        async with self._sessions() as session:
            said = await session.execute(
                sa.select(
                    sa.exists().where(
                        Message.conversation_id == conversation_id,
                        Message.direction == "out",
                        Message.text == text,
                        Message.created_at >= since,
                    )
                )
            )
            return said.scalar_one()

    async def bind_telegram_chat(self, chat_id: int, business_slug: str, now: datetime) -> None:
        """From now on the chat talks to this business (a later /start switches it)."""
        async with self._sessions.begin() as session:
            await session.execute(
                pg_insert(TelegramChat)
                .values(chat_id=chat_id, business_slug=business_slug, updated_at=now)
                .on_conflict_do_update(
                    index_elements=["chat_id"],
                    set_={"business_slug": business_slug, "updated_at": now},
                )
            )

    async def telegram_business(self, chat_id: int) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                sa.select(TelegramChat.business_slug).where(TelegramChat.chat_id == chat_id)
            )

    async def link_owner(self, business_slug: str, owner_chat_id: int) -> None:
        """Handoffs for this business go to this Telegram chat from now on."""
        async with self._sessions.begin() as session:
            await session.execute(
                pg_insert(BusinessConfig)
                .values(slug=business_slug, owner_telegram_chat_id=owner_chat_id)
                .on_conflict_do_update(
                    index_elements=["slug"], set_={"owner_telegram_chat_id": owner_chat_id}
                )
            )

    async def unlink_owner(self, business_slug: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                sa.update(BusinessConfig)
                .where(BusinessConfig.slug == business_slug)
                .values(owner_telegram_chat_id=None)
            )

    async def owner_chat(self, business_slug: str) -> int | None:
        async with self._sessions() as session:
            return await session.scalar(
                sa.select(BusinessConfig.owner_telegram_chat_id).where(
                    BusinessConfig.slug == business_slug
                )
            )

    async def recent_messages(self, conversation_id: int, limit: int) -> list[tuple[str, str]]:
        """The last `limit` messages between customer and assistant, oldest first."""
        async with self._sessions() as session:
            rows = await session.execute(
                sa.select(Message.direction, Message.text)
                .where(Message.conversation_id == conversation_id, Message.direction != "owner")
                .order_by(Message.seq.desc())
                .limit(limit)
            )
            return [(row.direction, row.text) for row in reversed(rows.all())]

    async def unsent_to_owner(self, conversation_id: int) -> list[OutboundMessage]:
        async with self._sessions() as session:
            rows = await session.execute(
                sa.select(Message.seq, Message.text)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.direction == "owner",
                    Message.sent_at.is_(None),
                )
                .order_by(Message.seq)
            )
            return [OutboundMessage(seq=row.seq, text=row.text) for row in rows]

    async def mark_relayed(
        self,
        seq: int,
        sent_message_id: str,
        owner_chat_id: int,
        conversation_id: int,
        now: datetime,
    ) -> None:
        """Mark an owner's message sent, and remember which conversation it
        speaks for, so the owner's reply to it finds its way back."""
        _, _, message_id = sent_message_id.partition(":")
        async with self._sessions.begin() as session:
            await session.execute(
                sa.update(Message)
                .where(Message.seq == seq)
                .values(sent_at=now, sent_message_id=sent_message_id)
            )
            if message_id.isdigit():
                await session.execute(
                    pg_insert(OwnerRelay)
                    .values(
                        owner_chat_id=owner_chat_id,
                        telegram_message_id=int(message_id),
                        conversation_id=conversation_id,
                    )
                    .on_conflict_do_nothing()
                )

    async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
        """Every write proposed in the conversation, and how it was answered."""
        async with self._sessions() as session:
            rows = await session.execute(
                sa.select(PendingApproval.tool_name, PendingApproval.decision)
                .where(PendingApproval.conversation_id == conversation_id)
                .order_by(PendingApproval.id)
            )
            return [(row.tool_name, cast(Decision | None, row.decision)) for row in rows]

    async def unsent(self, conversation_id: int) -> list[OutboundMessage]:
        async with self._sessions() as session:
            rows = await session.execute(
                sa.select(Message.seq, Message.text, Message.actions)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.direction == "out",
                    Message.sent_at.is_(None),
                )
                .order_by(Message.seq)
            )
            return [
                OutboundMessage(seq=row.seq, text=row.text, actions=_actions(row.actions))
                for row in rows
            ]

    async def mark_sent(self, seq: int, sent_message_id: str, now: datetime) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                sa.update(Message)
                .where(Message.seq == seq)
                .values(sent_at=now, sent_message_id=sent_message_id)
            )

    async def messages_after(
        self, business_slug: str, channel: ChannelName, channel_user_id: str, after: int
    ) -> MessagesPage:
        """The open conversation's messages after `after`, as its customer sees them."""
        async with self._sessions() as session:
            conversation_id = (
                await session.execute(
                    sa.select(Conversation.id).where(
                        Conversation.business_slug == business_slug,
                        Conversation.channel == channel,
                        Conversation.channel_user_id == channel_user_id,
                        Conversation.state != "closed",
                    )
                )
            ).scalar_one_or_none()
            if conversation_id is None:
                return MessagesPage(messages=[], pending=False)
            # The page stops short of the first reply not yet sent: a reader's
            # cursor that moved past it would never see it once it was.
            # min() over no rows is NULL, which the typed select cannot say.
            first_unsent = cast(
                int | None,
                (
                    await session.execute(
                        sa.select(sa.func.min(Message.seq)).where(
                            Message.conversation_id == conversation_id,
                            Message.direction == "out",
                            Message.sent_at.is_(None),
                        )
                    )
                ).scalar_one(),
            )
            shown = [Message.conversation_id == conversation_id, Message.seq > after]
            if first_unsent is not None:
                shown.append(Message.seq < first_unsent)
            rows = await session.execute(
                sa.select(Message)
                .where(
                    *shown,
                    sa.or_(
                        Message.direction == "in",
                        sa.and_(Message.direction == "out", Message.sent_at.is_not(None)),
                    ),
                )
                .order_by(Message.seq)
                .limit(PAGE_SIZE)
            )
            waiting = sa.or_(
                sa.and_(Message.direction == "in", Message.processed_at.is_(None)),
                sa.and_(Message.direction == "out", Message.sent_at.is_(None)),
            )
            pending = (
                await session.execute(
                    sa.select(
                        sa.exists().where(Message.conversation_id == conversation_id, waiting)
                    )
                )
            ).scalar_one()
            return MessagesPage(
                messages=[
                    PageMessage(
                        seq=row.seq,
                        direction="in" if row.direction == "in" else "out",
                        text=row.text,
                        actions=_actions(row.actions),
                        client_message_id=row.channel_message_id if row.direction == "in" else None,
                    )
                    for row in rows.scalars()
                ],
                pending=pending,
            )


def _actions(raw: Sequence[Any] | None) -> list[Action]:
    return [cast(Action, action) for action in raw or []]
