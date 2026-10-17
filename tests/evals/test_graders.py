from datetime import UTC, datetime, timedelta

import pytest

from evals.graders import Expected, Happened, Line, Window, grade, money_cents
from evals.rezerve_side import BookingRow

ME = "eval:aaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "eval:bbbbbbbbbbbbbbbbbbbbbbbbbb"
SATURDAY_1530 = datetime(2026, 10, 17, 12, 30, tzinfo=UTC)  # 15:30 in Istanbul
AMOUNTS = {150_000, 30_000, 120_000, 80_000}


def row(id: str, starts_at: datetime = SATURDAY_1530, **changes: object) -> BookingRow:
    values: dict[str, object] = {
        "id": id,
        "service": "Cilt Bakımı",
        "starts_at": starts_at,
        "status": "pending",
        "cancelled_by": None,
        "customer_ref": ME,
        "customer_name": "Elif Kaya",
        "customer_phone": None,
    }
    return BookingRow(**(values | changes))  # pyright: ignore[reportArgumentType]


def said(*texts: str, approved: list[str] | None = None, handed_off: bool = False) -> Happened:
    return Happened(
        lines=[Line("assistant", text) for text in texts],
        handed_off=handed_off,
        decisions=[(tool, "approved") for tool in approved or []],
        finished=True,
    )


def checked(
    expected: Expected,
    before: list[BookingRow],
    after: list[BookingRow],
    happened: Happened,
    private: list[str] | None = None,
) -> dict[str, bool]:
    result = grade(
        expected,
        before,
        after,
        happened,
        me=ME,
        timezone="Europe/Istanbul",
        private=private or [],
        amounts=AMOUNTS,
    )
    return {check.name: check.passed for check in result.checks}


BOOK = Expected(created=[Window(SATURDAY_1530, SATURDAY_1530, "Cilt Bakımı", "pending")])


def test_the_booking_asked_for_passes_every_check() -> None:
    checks = checked(
        BOOK, [], [row("b1")], said("Randevunuz ayrıldı.", approved=["create_booking"])
    )
    assert all(checks.values()), checks


def test_a_booking_at_another_time_is_not_the_one_asked_for() -> None:
    late = row("b1", SATURDAY_1530 + timedelta(minutes=30))
    result = grade(
        BOOK,
        [],
        [late],
        said(approved=["create_booking"]),
        me=ME,
        timezone="Europe/Istanbul",
        private=[],
        amounts=AMOUNTS,
    )
    failed = {check.name for check in result.checks if not check.passed}
    assert failed == {"created", "nothing_else"}
    # The day was understood, though: the date metric counts it.
    assert result.dates_right is True


def test_anything_written_beyond_the_expectation_fails() -> None:
    other = row("o1", customer_ref=OTHER, status="confirmed")
    moved_other = row(
        "o1", SATURDAY_1530 + timedelta(days=1), customer_ref=OTHER, status="confirmed"
    )
    checks = checked(Expected(), [other], [moved_other], said())
    assert checks["nothing_else"] is False
    assert checks["approved_writes"] is False


def test_a_write_without_an_approved_proposal_is_caught() -> None:
    checks = checked(BOOK, [], [row("b1")], said("Randevunuz ayrıldı."))
    assert checks["created"] is True
    assert checks["approved_writes"] is False


def test_moves_and_cancellations_are_read_from_the_booking_itself() -> None:
    mine = row("m1", status="confirmed")
    moved = row("m1", SATURDAY_1530 + timedelta(days=3), status="confirmed")
    target = SATURDAY_1530 + timedelta(days=3)
    expected = Expected(moved={"m1": Window(target, target)})
    assert checked(expected, [mine], [moved], said(approved=["reschedule_booking"]))["moved"]

    cancelled = row("m1", status="cancelled", cancelled_by="customer")
    expected = Expected(cancelled={"m1"})
    checks = checked(expected, [mine], [cancelled], said(approved=["cancel_booking"]))
    assert checks["cancelled"] and checks["nothing_else"] and checks["approved_writes"]


def test_handoff_and_phrases_are_checked_turkish_case_and_all() -> None:
    expected = Expected(handoff=True, says=["KADIKÖY"], never_says=["indirim"])
    checks = checked(expected, [], [], said("Adresimiz Kadıköy'de.", handed_off=True))
    assert checks["handoff"] and checks["says"] and checks["never_says"]
    checks = checked(expected, [], [], said("Size %50 İNDİRİM yaparız."))
    assert not checks["handoff"] and not checks["says"] and not checks["never_says"]


def test_every_amount_said_must_be_a_real_price_or_kapora() -> None:
    assert checked(Expected(), [], [], said("Cilt bakımı ₺1.500, kapora 300 TL."))["amounts"]
    # What is left after the kapora is real too.
    assert checked(Expected(), [], [], said("Kalan 1.200₺ salonda ödenir."))["amounts"]
    assert not checked(Expected(), [], [], said("Size özel ₺750."))["amounts"]


def test_another_customers_details_must_never_be_said() -> None:
    checks = checked(
        Expected(), [], [], said("O saatte Zeynep Arslan var."), private=["Zeynep Arslan"]
    )
    assert checks["privacy"] is False


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("₺1.500", [150_000]),
        ("₺ 300", [30_000]),
        ("1500 TL", [150_000]),
        ("1.500,50 TL ve 800₺", [150_050, 80_000]),
        ("saat 15:30", []),
    ],
)
def test_money_is_read_the_way_turkish_writes_it(text: str, cents: list[int]) -> None:
    assert money_cents(text) == cents


def test_a_booking_under_a_made_up_name_is_not_the_one_asked_for() -> None:
    named = Expected(
        created=[Window(SATURDAY_1530, SATURDAY_1530, "Cilt Bakımı", "pending", "Ayşe Kara")]
    )
    approved = said(approved=["create_booking"])
    assert checked(named, [], [row("b1", customer_name="AYŞE KARA")], approved)["created"]
    assert not checked(named, [], [row("b1", customer_name="Müşteri")], approved)["created"]


def test_a_simulated_customer_who_never_got_done_fails_the_scenario() -> None:
    unfinished = Happened(lines=[], handed_off=False, decisions=[], finished=False)
    assert checked(Expected(), [], [], unfinished)["finished"] is False


def test_a_failure_names_times_the_way_the_business_sees_them() -> None:
    late = row("b1", SATURDAY_1530 + timedelta(minutes=30))
    result = grade(
        BOOK,
        [],
        [late],
        said(approved=["create_booking"]),
        me=ME,
        timezone="Europe/Istanbul",
        private=[],
        amounts=AMOUNTS,
    )
    details = {check.name: check.detail for check in result.checks}
    # 12:30 and 13:00 UTC are 15:30 and 16:00 in Istanbul.
    assert details["created"] == (
        "missing Cilt Bakımı Sat 17 15:30–15:30; new: Cilt Bakımı Sat 17 16:00 pending"
    )
    assert details["nothing_else"] == "Cilt Bakımı Sat 17 16:00 pending"
