from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from evals.when import Clock, UnknownDay, render, resolve, resolve_day

ISTANBUL = ZoneInfo("Europe/Istanbul")
# Thursday 15 October 2026, 11:55 in Istanbul; bookings need an hour's notice.
THURSDAY = Clock.at(
    datetime(2026, 10, 15, 8, 55, tzinfo=UTC), "Europe/Istanbul", timedelta(hours=1)
)


@pytest.mark.parametrize(
    ("expression", "day"),
    [
        ("today", date(2026, 10, 15)),
        ("tomorrow", date(2026, 10, 16)),
        ("day after tomorrow", date(2026, 10, 17)),
        ("in 9 days", date(2026, 10, 24)),
        ("next saturday", date(2026, 10, 17)),
        ("next thursday", date(2026, 10, 15)),
        # "haftaya salı": the Tuesday of next week, not the next Tuesday to come.
        ("tuesday next week", date(2026, 10, 20)),
        ("monday next week", date(2026, 10, 19)),
        ("thursday next week", date(2026, 10, 22)),
        # "2 hafta sonra salı": the Tuesday of the week after next.
        ("tuesday in two weeks", date(2026, 10, 27)),
        ("thursday in two weeks", date(2026, 10, 29)),
        ("  Next   SATURDAY ", date(2026, 10, 17)),
    ],
)
def test_a_day_is_named_the_same_on_any_run(expression: str, day: date) -> None:
    assert resolve_day(expression, THURSDAY) == day


def test_next_weekday_is_today_only_while_the_time_is_still_bookable() -> None:
    # 11:55 plus the hour's notice is 12:55: 14:00 still works today, 12:30 does not.
    assert resolve("next thursday", "14:00", THURSDAY) == datetime(
        2026, 10, 15, 14, 0, tzinfo=ISTANBUL
    )
    assert resolve("next thursday", "12:30", THURSDAY) == datetime(
        2026, 10, 22, 12, 30, tzinfo=ISTANBUL
    )


def test_an_unknown_day_is_a_mistake_in_the_scenario() -> None:
    with pytest.raises(UnknownDay, match="'haftaya salı'"):
        resolve_day("haftaya salı", THURSDAY)


def test_a_scripted_line_names_dates_the_way_a_customer_does() -> None:
    assert render("{date:in 13 days} saat 3 uygun mu?", THURSDAY) == "28 Ekim saat 3 uygun mu?"
    assert render("yarın olur", THURSDAY) == "yarın olur"


def test_a_scripted_line_can_name_a_weekday_the_way_a_customer_does() -> None:
    # "perşembe", written on a Thursday: the same-weekday case, on any run day.
    assert render("{weekday:today} 10:00 olur mu?", THURSDAY) == "perşembe 10:00 olur mu?"
    assert render("{weekday:in 2 days}", THURSDAY) == "cumartesi"


def test_the_same_weekday_is_today_only_while_the_time_is_still_bookable() -> None:
    assert resolve("next same weekday", "14:00", THURSDAY) == datetime(
        2026, 10, 15, 14, 0, tzinfo=ISTANBUL
    )
    assert resolve("next same weekday", "10:00", THURSDAY) == datetime(
        2026, 10, 22, 10, 0, tzinfo=ISTANBUL
    )
