"""Against Rezerve's own eval database, when one is set up on this machine
(README, "Evals"); skipped otherwise, as in CI."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from evals.rezerve_side import NewBooking, RezerveDb, local_only

REZERVE_EVAL_URL = os.environ.get(
    "REZERVE_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/rezerve_eval"
)
NEXT_WEEK = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=7)


@pytest.fixture
async def rezerve() -> AsyncIterator[RezerveDb]:
    database = RezerveDb(REZERVE_EVAL_URL)
    try:
        await database.business()
    except Exception as error:
        await database.aclose()
        pytest.skip(f"Rezerve's eval database is not set up: {error}")
    yield database
    await database.reset(None)
    await database.aclose()


async def test_the_eval_business_is_read_as_rezerve_keeps_it(rezerve: RezerveDb) -> None:
    business = await rezerve.business()
    assert business.timezone == "Europe/Istanbul"
    # Monday to Saturday, with a lunch break; closed on Sunday.
    assert business.hours[0] == [(600, 780), (840, 1140)]
    assert sorted(business.hours) == [0, 1, 2, 3, 4, 5]
    assert business.services["Cilt Bakımı"].deposit_cents == 30_000
    assert business.services["Saç Kesimi"].deposit_cents is None


async def test_bookings_are_seeded_read_back_and_cleared(rezerve: RezerveDb) -> None:
    await rezerve.reset("Pazar günleri kapalıyız.")
    ids = await rezerve.seed(
        [
            NewBooking(
                key="mine",
                service="Cilt Bakımı",
                starts_at=NEXT_WEEK,
                customer_ref="eval:" + "a" * 26,
                name="Elif Kaya",
                phone=None,
                status="confirmed",
                paid=True,
            ),
            NewBooking(
                key="theirs",
                service="Saç Kesimi",
                starts_at=NEXT_WEEK + timedelta(hours=2),
                customer_ref="eval:" + "b" * 26,
                name="Zeynep Arslan",
                phone="0532 111 22 33",
                status="confirmed",
                paid=False,
            ),
        ]
    )
    rows = {row.id: row for row in await rezerve.snapshot()}
    assert set(rows) == set(ids.values())
    mine = rows[ids["mine"]]
    assert (mine.service, mine.starts_at, mine.status) == ("Cilt Bakımı", NEXT_WEEK, "confirmed")
    assert rows[ids["theirs"]].customer_phone == "0532 111 22 33"

    await rezerve.reset(None)
    assert await rezerve.snapshot() == []


def test_only_a_database_on_this_machine_is_ever_touched() -> None:
    with pytest.raises(ValueError, match="local only"):
        local_only("postgresql://user:secret@ep-example.eu-central-1.aws.neon.tech/neondb")
    assert local_only("postgresql://postgres@localhost:5433/rezerve_eval").startswith(
        "postgresql+asyncpg://"
    )
