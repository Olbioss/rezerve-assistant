"""Sending a reminder when its task fires (spec §7.7): only for a booking
still confirmed at the time it was booked for, and only once."""

from datetime import UTC, datetime, timedelta

import pytest

from assistant.agent.format import local_label
from assistant.conversation.store import Store
from assistant.identity import customer_ref
from assistant.reminders import Reminders
from tests.fakes.channels import TelegramOutbox
from tests.fakes.rezerve import SAC, TIMEZONE, FakeRezerve

REF = customer_ref("secret", "telegram", "4242")
IN_THREE_DAYS = (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def fake() -> FakeRezerve:
    return FakeRezerve()


@pytest.fixture
def telegram() -> TelegramOutbox:
    return TelegramOutbox()


@pytest.fixture
def reminders(store: Store, fake: FakeRezerve, telegram: TelegramOutbox) -> Reminders:
    return Reminders(store, lambda slug, ref: fake.session(), telegram)


async def test_a_booking_still_at_its_time_is_reminded_once(
    store: Store, fake: FakeRezerve, telegram: TelegramOutbox, reminders: Reminders
) -> None:
    booking = fake.add_booking(SAC, IN_THREE_DAYS)
    assert await store.add_reminder(booking.id, IN_THREE_DAYS, "demo", REF, 4242, datetime.now(UTC))

    await reminders.send(booking.id, IN_THREE_DAYS)
    await reminders.send(booking.id, IN_THREE_DAYS)  # Cloud Tasks delivers at least once

    assert telegram.sent == [
        (
            "4242",
            "Hatırlatma: Saç Kesimi randevunuz yaklaşıyor — "
            f"{local_label(IN_THREE_DAYS, TIMEZONE)}. Görüşmek üzere!",
        )
    ]


@pytest.mark.parametrize("change", ["moved", "cancelled", "unpaid"])
async def test_a_booking_that_changed_since_is_not_reminded(
    store: Store, fake: FakeRezerve, telegram: TelegramOutbox, reminders: Reminders, change: str
) -> None:
    # A move schedules a reminder of its own; a cancelled or never-paid
    # booking has nothing to remind of.
    booking = fake.add_booking(
        SAC, IN_THREE_DAYS, status="pending" if change == "unpaid" else "confirmed"
    )
    if change == "moved":
        booking.starts_at += timedelta(hours=2)
    if change == "cancelled":
        booking.status = "cancelled"
    await store.add_reminder(booking.id, IN_THREE_DAYS, "demo", REF, 4242, datetime.now(UTC))

    await reminders.send(booking.id, IN_THREE_DAYS)

    assert telegram.sent == []


async def test_a_reminder_never_scheduled_sends_nothing(
    telegram: TelegramOutbox, reminders: Reminders
) -> None:
    await reminders.send("no-such-booking", IN_THREE_DAYS)
    assert telegram.sent == []
