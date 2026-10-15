from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic_ai import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from sqlalchemy.exc import IntegrityError, NoResultFound

from assistant.channels.base import InboundMessage, LinkAction
from assistant.conversation.store import NewApproval, Reply, Store, TurnResult

NOW = datetime.now(UTC)
REF = "web:" + "a" * 26


def inbound(
    text: str = "merhaba", *, user: str = "visitor-1", slug: str = "demo"
) -> InboundMessage:
    return InboundMessage(
        business_slug=slug,
        channel="web",
        channel_user_id=user,
        channel_message_id=str(uuid4()),
        text=text,
    )


async def test_a_redelivered_message_is_stored_once(store: Store) -> None:
    message = inbound()
    first = await store.record_inbound(message, REF, NOW)
    again = await store.record_inbound(message, REF, NOW)
    assert first.seq is not None
    assert again.seq is None
    assert again.conversation_id == first.conversation_id
    assert [m.text for m in await store.unprocessed_inbound(first.conversation_id)] == ["merhaba"]


async def test_one_customer_keeps_one_open_conversation_per_business(store: Store) -> None:
    first = await store.record_inbound(inbound("merhaba"), REF, NOW)
    second = await store.record_inbound(inbound("yer var mı?"), REF, NOW)
    other_business = await store.record_inbound(inbound(slug="baska-salon"), REF, NOW)
    other_customer = await store.record_inbound(inbound(user="visitor-2"), REF, NOW)
    assert second.conversation_id == first.conversation_id
    assert (
        len({first.conversation_id, other_business.conversation_id, other_customer.conversation_id})
        == 3
    )


async def test_a_day_idle_conversation_closes_and_the_next_message_starts_afresh(
    store: Store,
) -> None:
    old = await store.record_inbound(inbound("eski"), REF, NOW - timedelta(hours=25))
    new = await store.record_inbound(inbound("yeni"), REF, NOW)
    assert new.conversation_id != old.conversation_id
    page = await store.messages_after("demo", "web", "visitor-1", 0)
    assert [m.text for m in page.messages] == ["yeni"]


async def test_transcripts_idle_past_retention_go_when_a_conversation_opens(
    store: Store,
) -> None:
    stale = await store.record_inbound(inbound(user="gone"), REF, NOW - timedelta(days=91))
    kept = await store.record_inbound(inbound(user="recent"), REF, NOW - timedelta(days=89))
    await store.record_inbound(inbound(user="newcomer"), REF, NOW)
    with pytest.raises(NoResultFound):
        await store.conversation(stale.conversation_id)
    assert (await store.conversation(kept.conversation_id)).channel_user_id == "recent"


async def test_the_page_shows_a_reply_once_it_is_sent(store: Store) -> None:
    message = inbound()
    recorded = await store.record_inbound(message, REF, NOW)
    assert recorded.seq is not None

    page = await store.messages_after("demo", "web", "visitor-1", 0)
    assert [(m.direction, m.client_message_id) for m in page.messages] == [
        ("in", message.channel_message_id)
    ]
    assert page.pending

    link: LinkAction = {"type": "link", "label": "Kaporayı öde", "url": "https://pay.example/1"}
    reply = Reply("Merhaba!", [link])
    await store.commit_turn(
        recorded.conversation_id, TurnResult(processed=[recorded.seq], replies=[reply]), NOW
    )
    page = await store.messages_after("demo", "web", "visitor-1", 0)
    assert [m.direction for m in page.messages] == ["in"]
    assert page.pending  # answered, not yet sent

    [unsent] = await store.unsent(recorded.conversation_id)
    await store.mark_sent(unsent.seq, f"web:{unsent.seq}", NOW)
    page = await store.messages_after("demo", "web", "visitor-1", 0)
    assert [(m.direction, m.text, m.actions) for m in page.messages] == [
        ("in", "merhaba", []),
        ("out", "Merhaba!", [link]),
    ]
    assert not page.pending
    after = await store.messages_after("demo", "web", "visitor-1", page.messages[-1].seq)
    assert after.messages == []


async def test_a_turn_commits_history_approval_and_run_together(store: Store) -> None:
    recorded = await store.record_inbound(inbound("randevu"), REF, NOW)
    assert recorded.seq is not None
    messages = [
        ModelRequest(parts=[UserPromptPart("randevu")]),
        ModelResponse(parts=[ToolCallPart("create_booking", {"x": 1}, tool_call_id="c1")]),
    ]
    approval = NewApproval(
        tool_call_id="c1",
        tool_name="create_booking",
        args={"x": 1},
        other_call_ids=["c2"],
        rendered_text="Onaylıyor musunuz?",
        expires_at=NOW + timedelta(minutes=10),
    )
    await store.commit_turn(
        recorded.conversation_id,
        TurnResult(processed=[recorded.seq], replies=[], history=messages, approval=approval),
        NOW,
    )
    opened = await store.open_approval(recorded.conversation_id)
    assert opened is not None
    assert (opened.tool_call_id, opened.args, opened.other_call_ids) == ("c1", {"x": 1}, ["c2"])
    assert len(opened.history) == 2
    assert len((await store.conversation(recorded.conversation_id)).history) == 2
    assert await store.unprocessed_inbound(recorded.conversation_id) == []

    await store.commit_turn(
        recorded.conversation_id,
        TurnResult(processed=[], replies=[], resolved=(opened.id, "approved")),
        NOW,
    )
    assert await store.open_approval(recorded.conversation_id) is None


async def test_a_conversation_holds_one_open_approval(store: Store) -> None:
    recorded = await store.record_inbound(inbound(), REF, NOW)
    approval = NewApproval("c1", "create_booking", {}, [], "?", NOW + timedelta(minutes=10))
    result = TurnResult(processed=[], replies=[], history=[], approval=approval)
    await store.commit_turn(recorded.conversation_id, result, NOW)
    with pytest.raises(IntegrityError):
        await store.commit_turn(recorded.conversation_id, result, NOW)


async def test_the_page_never_moves_past_a_reply_not_yet_sent(store: Store) -> None:
    recorded = await store.record_inbound(inbound("merhaba"), REF, NOW)
    assert recorded.seq is not None
    answered = TurnResult(processed=[recorded.seq], replies=[Reply("Merhaba!")])
    await store.commit_turn(recorded.conversation_id, answered, NOW)
    # The customer writes again after the reply is stored, before it is sent.
    await store.record_inbound(inbound("yarın yer var mı?"), REF, NOW)

    page = await store.messages_after("demo", "web", "visitor-1", 0)
    assert [m.text for m in page.messages] == ["merhaba"]
    cursor = page.messages[-1].seq

    [reply] = await store.unsent(recorded.conversation_id)
    await store.mark_sent(reply.seq, f"web:{reply.seq}", NOW)
    page = await store.messages_after("demo", "web", "visitor-1", cursor)
    assert [m.text for m in page.messages] == ["Merhaba!", "yarın yer var mı?"]
