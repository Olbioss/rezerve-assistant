"""Rezerve as the harness sees it: the eval business, reset and seeded before
each scenario, and its bookings read afterwards to grade it (spec §8.2).

The agent itself only ever reaches Rezerve through MCP. This side goes
straight to Rezerve's local database, the way an examiner checks the result
rather than asking the candidate. It refuses any database not on this
machine: it deletes bookings.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

EVAL_ORG = "org_rezerve_eval"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    duration_minutes: int
    price_cents: int
    deposit_cents: int | None


@dataclass(frozen=True)
class Business:
    timezone: str
    min_lead: timedelta
    booking_window_days: int
    # Python weekday (Monday is 0) → open spans, in minutes after midnight.
    hours: dict[int, list[tuple[int, int]]]
    services: dict[str, Service]


@dataclass(frozen=True)
class BookingRow:
    id: str
    service: str
    starts_at: datetime
    status: str
    cancelled_by: str | None
    customer_ref: str | None
    customer_name: str
    customer_phone: str | None


@dataclass(frozen=True)
class NewBooking:
    """A seed, resolved to an instant and a customer."""

    key: str
    service: str
    starts_at: datetime
    customer_ref: str
    name: str
    phone: str | None
    status: str
    paid: bool


class RezerveSide(Protocol):
    async def business(self) -> Business: ...

    async def reset(self, notes: str | None) -> None:
        """No bookings, no throttle history, and these notes for the assistant."""
        ...

    async def seed(self, bookings: list[NewBooking]) -> dict[str, str]:
        """Insert the bookings; returns each one's id by its key."""
        ...

    async def snapshot(self) -> list[BookingRow]: ...


def local_only(url: str) -> str:
    """The URL for asyncpg, provided it names a database on this machine."""
    host = urlsplit(url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise ValueError(f"Refusing Rezerve's database on {host or 'no host'!r}: local only.")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


class RezerveDb:
    def __init__(self, url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(local_only(url))

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def business(self) -> Business:
        async with self._engine.connect() as connection:
            profile = (
                await connection.execute(
                    sa.text(
                        "SELECT timezone, min_lead_time_minutes, booking_window_days "
                        "FROM business_profiles WHERE organization_id = :org"
                    ),
                    {"org": EVAL_ORG},
                )
            ).one_or_none()
            if profile is None:
                raise LookupError(
                    "No eval business in Rezerve's database: run `bun run seed:eval` in Rezerve."
                )
            rules = await connection.execute(
                sa.text(
                    "SELECT weekday, start_minutes, end_minutes FROM availability_rules "
                    "WHERE organization_id = :org ORDER BY weekday, start_minutes"
                ),
                {"org": EVAL_ORG},
            )
            hours: dict[int, list[tuple[int, int]]] = {}
            for rule in rules:
                # Rezerve counts weekdays from Sunday (0), as JavaScript does.
                hours.setdefault((rule.weekday - 1) % 7, []).append(
                    (rule.start_minutes, rule.end_minutes)
                )
            services = await connection.execute(
                sa.text(
                    "SELECT id::text, name, duration_minutes, price_cents, deposit_cents "
                    "FROM services WHERE organization_id = :org AND active"
                ),
                {"org": EVAL_ORG},
            )
            return Business(
                timezone=profile.timezone,
                min_lead=timedelta(minutes=profile.min_lead_time_minutes),
                booking_window_days=profile.booking_window_days,
                hours=hours,
                services={row.name: Service(*row) for row in services},
            )

    async def reset(self, notes: str | None) -> None:
        async with self._engine.begin() as connection:
            for table in ("bookings", "booking_attempts"):
                await connection.execute(
                    sa.text(f"DELETE FROM {table} WHERE organization_id = :org"), {"org": EVAL_ORG}
                )
            await connection.execute(
                sa.text(
                    "UPDATE business_profiles SET assistant_notes = :notes "
                    "WHERE organization_id = :org"
                ),
                {"org": EVAL_ORG, "notes": notes},
            )

    async def seed(self, bookings: list[NewBooking]) -> dict[str, str]:
        if not bookings:
            return {}
        services = (await self.business()).services
        ids: dict[str, str] = {}
        async with self._engine.begin() as connection:
            for booking in bookings:
                service = services[booking.service]
                inserted = await connection.execute(
                    sa.text(
                        "INSERT INTO bookings (organization_id, service_id, customer_name, "
                        "customer_phone, starts_at, ends_at, status, deposit_cents, "
                        "payment_transaction_id, expires_at, source, customer_ref) "
                        "VALUES (:org, CAST(:service_id AS uuid), :name, :phone, :starts_at, "
                        ":ends_at, CAST(:status AS booking_status), :deposit, :payment, "
                        ":expires_at, 'assistant', :ref) RETURNING id::text"
                    ),
                    {
                        "org": EVAL_ORG,
                        "service_id": service.id,
                        "name": booking.name,
                        "phone": booking.phone,
                        "starts_at": booking.starts_at,
                        "ends_at": booking.starts_at + timedelta(minutes=service.duration_minutes),
                        "status": booking.status,
                        "deposit": service.deposit_cents,
                        # Paid as far as Rezerve can tell; no real payment exists.
                        "payment": "eval-seed" if booking.paid else None,
                        "expires_at": datetime.now(UTC) + timedelta(minutes=30)
                        if booking.status == "pending"
                        else None,
                        "ref": booking.customer_ref,
                    },
                )
                ids[booking.key] = inserted.scalar_one()
        return ids

    async def snapshot(self) -> list[BookingRow]:
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                sa.text(
                    "SELECT b.id::text, s.name, b.starts_at, b.status::text, "
                    "b.cancelled_by::text, b.customer_ref, b.customer_name, b.customer_phone "
                    "FROM bookings b JOIN services s ON s.id = b.service_id "
                    "WHERE b.organization_id = :org ORDER BY b.created_at, b.id"
                ),
                {"org": EVAL_ORG},
            )
            return [BookingRow(*row) for row in rows]
