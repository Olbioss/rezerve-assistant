from datetime import UTC, date, datetime

import pytest

from assistant.agent.calendar import calendar_block
from assistant.agent.format import format_money, local_label


def test_a_label_reads_like_rezerves() -> None:
    # Istanbul is UTC+3 all year.
    instant = datetime(2026, 10, 17, 12, 30, tzinfo=UTC)
    assert local_label(instant, "Europe/Istanbul") == "Cumartesi 17 Ekim 15:30"


def test_a_label_takes_the_date_from_the_business_timezone() -> None:
    instant = datetime(2026, 10, 17, 21, 30, tzinfo=UTC)
    assert local_label(instant, "Europe/Istanbul") == "Pazar 18 Ekim 00:30"


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (30_000, "₺300"),
        (150_000, "₺1.500"),
        (12_345, "₺123,45"),
        (50, "₺0,50"),
        (123_456_700, "₺1.234.567"),
    ],
)
def test_money_is_grouped_the_turkish_way(cents: int, text: str) -> None:
    assert format_money(cents, "try") == text


def test_an_unknown_currency_shows_its_code() -> None:
    assert format_money(1_000, "eur") == "EUR 10"


def test_the_calendar_covers_two_weeks_from_today() -> None:
    lines = calendar_block(date(2026, 10, 15)).splitlines()
    assert len(lines) == 14
    assert lines[0] == "2026-10-15 Perşembe (bugün)"
    assert lines[1] == "2026-10-16 Cuma (yarın)"
    assert lines[6] == "2026-10-21 Çarşamba"
    assert lines[13] == "2026-10-28 Çarşamba"
