import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from assistant.channels.base import InboundMessage
from assistant.conversation.lease import Lease, LeaseBusy, LeaseLost
from assistant.conversation.store import Store

MINUTE = timedelta(minutes=1)


async def conversation(store: Store) -> int:
    message = InboundMessage("demo", "web", "visitor-1", str(uuid4()), "merhaba")
    recorded = await store.record_inbound(message, "web:" + "a" * 26, datetime.now(UTC))
    return recorded.conversation_id


async def test_handlers_racing_for_one_conversation_let_one_in(store: Store) -> None:
    conversation_id = await conversation(store)
    claims = await asyncio.gather(
        *(store.claim_lease(conversation_id, f"handler-{n}", MINUTE) for n in range(5))
    )
    assert claims.count(True) == 1


async def test_a_held_lease_turns_others_away_until_released(store: Store) -> None:
    conversation_id = await conversation(store)
    async with Lease(store, conversation_id, ttl=MINUTE, renew_every=MINUTE):
        with pytest.raises(LeaseBusy):
            async with Lease(store, conversation_id, ttl=MINUTE, renew_every=MINUTE):
                pass
    async with Lease(store, conversation_id, ttl=MINUTE, renew_every=MINUTE):
        pass


async def test_a_crashed_handlers_lease_lapses_on_its_own(store: Store) -> None:
    conversation_id = await conversation(store)
    assert await store.claim_lease(conversation_id, "crashed", timedelta(milliseconds=100))
    await asyncio.sleep(0.2)
    assert await store.claim_lease(conversation_id, "next", MINUTE)


async def test_renewal_keeps_a_slow_run_safe(store: Store) -> None:
    conversation_id = await conversation(store)
    lease = Lease(
        store, conversation_id, ttl=timedelta(seconds=0.6), renew_every=timedelta(seconds=0.2)
    )
    async with lease:
        await asyncio.sleep(1.0)
        assert not await store.claim_lease(conversation_id, "intruder", MINUTE)
        lease.check()


async def test_a_lapsed_lease_stops_its_handler_and_spares_the_new_one(store: Store) -> None:
    conversation_id = await conversation(store)
    lease = Lease(
        store, conversation_id, ttl=timedelta(seconds=0.3), renew_every=timedelta(seconds=0.5)
    )
    async with lease:
        await asyncio.sleep(0.4)  # lapsed before its first renewal
        assert await store.claim_lease(conversation_id, "successor", MINUTE)
        await asyncio.sleep(0.3)  # the renewal at 0.5s finds the lease gone
        with pytest.raises(LeaseLost):
            lease.check()
    # Leaving released nothing: the successor still holds it.
    assert not await store.claim_lease(conversation_id, "third", MINUTE)


async def test_a_renewal_that_errors_counts_as_lost_and_the_lease_is_still_released(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await conversation(store)

    async def database_gone(*args: object) -> bool:
        raise ConnectionError("the database went away")

    lease = Lease(
        store, conversation_id, ttl=timedelta(seconds=5), renew_every=timedelta(seconds=0.1)
    )
    async with lease:
        monkeypatch.setattr(store, "renew_lease", database_gone)
        await asyncio.sleep(0.3)
        with pytest.raises(LeaseLost):
            lease.check()
    monkeypatch.undo()
    # Leaving released it anyway, so the next handler need not wait out the TTL.
    assert await store.claim_lease(conversation_id, "next", MINUTE)
