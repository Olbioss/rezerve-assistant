"""The eval business in memory: FakeRezerve's bookings, reset, seeded and
read the way the harness does Rezerve's database."""

from datetime import timedelta

from evals.rezerve_side import BookingRow, Business, NewBooking, Service
from tests.fakes.rezerve import SERVICES, TIMEZONE, FakeRezerve

# The eval business's hours: Monday to Saturday, 10:00–13:00 and 14:00–19:00.
HOURS = {weekday: [(10 * 60, 13 * 60), (14 * 60, 19 * 60)] for weekday in range(6)}


class FakeSide:
    def __init__(self, fake: FakeRezerve, me: str) -> None:
        self.fake = fake
        # FakeRezerve cannot tell customers apart: what it books is this customer's.
        self.me = me
        self.refs: dict[str, str] = {}
        self.notes: str | None = None

    async def business(self) -> Business:
        return Business(
            timezone=TIMEZONE,
            min_lead=timedelta(minutes=60),
            booking_window_days=30,
            hours=HOURS,
            services={
                service["name"]: Service(
                    id=service["id"],
                    name=service["name"],
                    duration_minutes=service["duration_minutes"],
                    price_cents=service["price_cents"],
                    deposit_cents=service["deposit_cents"],
                )
                for service in SERVICES
            },
        )

    async def reset(self, notes: str | None) -> None:
        self.fake.bookings.clear()
        self.refs.clear()
        self.notes = notes

    async def seed(self, bookings: list[NewBooking]) -> dict[str, str]:
        services = (await self.business()).services
        ids: dict[str, str] = {}
        for booking in bookings:
            made = self.fake.add_booking(
                services[booking.service].id,
                booking.starts_at,
                status=booking.status,
                paid=booking.paid,
            )
            made.customer_name = booking.name
            self.refs[made.id] = booking.customer_ref
            ids[booking.key] = made.id
        return ids

    async def snapshot(self) -> list[BookingRow]:
        names = {service["id"]: service["name"] for service in SERVICES}
        return [
            BookingRow(
                id=booking.id,
                service=names[booking.service_id],
                starts_at=booking.starts_at,
                status=booking.status,
                cancelled_by=booking.cancelled_by,
                customer_ref=self.refs.get(booking.id, self.me),
                customer_name=booking.customer_name,
                customer_phone=None,
            )
            for booking in self.fake.bookings.values()
        ]
