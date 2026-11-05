"""Reminders (spec §7.7): a day before a booking the agent made or moved, on
Telegram. WhatsApp reminders need paid templates (v2), and a web visitor
cannot be reached once the page is closed.

A move schedules a reminder of its own, and the old one finds, when it fires,
that the booking no longer starts when it said: it sends nothing. Nor does
one for a booking cancelled since, or never paid for.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from assistant.channels.base import Channel, OutboundMessage
from assistant.conversation.store import Store
from assistant.mcp import BookingView, CompletedWrite, OpenRezerve, RezerveError

log = logging.getLogger(__name__)

REMIND_BEFORE = timedelta(hours=24)
# A reminder due sooner than this is no reminder: the booking was just made.
MIN_NOTICE = timedelta(hours=1)
MOVES = frozenset({"create_booking", "reschedule_booking"})


def booked(writes: Sequence[CompletedWrite]) -> list[tuple[str, datetime]]:
    """Each booking these writes made or moved, and when it now starts."""
    found: list[tuple[str, datetime]] = []
    for write in writes:
        result = write.result or {}
        if write.tool_name in MOVES and "booking_id" in result and "starts_at" in result:
            starts_at = datetime.fromisoformat(str(result["starts_at"]))
            found.append((str(result["booking_id"]), starts_at))
    return found


def reminder_text(booking: BookingView) -> str:
    return (
        f"Hatırlatma: {booking.service_name} randevunuz yaklaşıyor — "
        f"{booking.local_label}. Görüşmek üzere!"
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Reminders:
    """Sends a reminder when its task fires."""

    def __init__(
        self,
        store: Store,
        open_rezerve: OpenRezerve,
        telegram: Channel | None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._open_rezerve = open_rezerve
        self._telegram = telegram
        self._clock = clock

    async def send(self, booking_id: str, starts_at: datetime) -> None:
        due = await self._store.reminder(booking_id, starts_at)
        if due is None or due.sent_at is not None or self._telegram is None:
            return
        try:
            async with self._open_rezerve(due.business_slug, due.customer_ref) as rezerve:
                booking = await rezerve.booking(booking_id)
        except RezerveError as error:
            log.info("No reminder for booking %s: %s", booking_id, error.code)
            return
        if booking.status != "confirmed" or booking.starts_at != starts_at:
            return
        message = OutboundMessage(seq=0, text=reminder_text(booking))
        sent = await self._telegram.send(str(due.chat_id), message)
        await self._store.mark_reminded(booking_id, starts_at, sent, self._clock())
