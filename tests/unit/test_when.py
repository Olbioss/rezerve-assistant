from datetime import date

import pytest

from assistant.agent.when import When, resolve_when

THURSDAY = date(2026, 10, 15)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("bugün", When(date(2026, 10, 15), "Perşembe")),
        ("YARIN", When(date(2026, 10, 16), "Cuma")),
        ("yarın akşam", When(date(2026, 10, 16), "Cuma", "18:00", "21:00")),
        ("yarın öğlen", When(date(2026, 10, 16), "Cuma", "12:00", "14:00")),
        ("öbür gün sabah", When(date(2026, 10, 17), "Cumartesi", "09:00", "12:00")),
        ("öbür gün öğleden önce", When(date(2026, 10, 17), "Cumartesi", "09:00", "12:00")),
        ("haftaya salı", When(date(2026, 10, 20), "Salı")),
        ("haftaya salıya", When(date(2026, 10, 20), "Salı")),
        ("gelecek hafta pazartesi", When(date(2026, 10, 19), "Pazartesi")),
        ("haftaya perşembe", When(date(2026, 10, 22), "Perşembe")),
        ("bu cuma", When(date(2026, 10, 16), "Cuma")),
        ("perşembe", When(date(2026, 10, 15), "Perşembe")),
        ("pazar", When(date(2026, 10, 18), "Pazar")),
        ("cumartesiye", When(date(2026, 10, 17), "Cumartesi")),
        ("cumartesi öğleden sonra", When(date(2026, 10, 17), "Cumartesi", "14:00", "18:00")),
        ("çarşamba akşamüstü", When(date(2026, 10, 21), "Çarşamba", "16:00", "19:00")),
        ("hafta sonu", When(date(2026, 10, 17), "Cumartesi")),
        ("ayın 17'si", When(date(2026, 10, 17), "Cumartesi")),
        ("ayın 3'ü", When(date(2026, 11, 3), "Salı")),
        ("28 Ekim", When(date(2026, 10, 28), "Çarşamba")),
        ("28 ekimde akşamüstü", When(date(2026, 10, 28), "Çarşamba", "16:00", "19:00")),
        ("3 Ocak", When(date(2027, 1, 3), "Pazar")),
        ("31 Şubat", None),
        ("öğleden sonra", None),
        ("merhaba", None),
    ],
)
def test_a_turkish_day_is_resolved_by_rule(expression: str, expected: When | None) -> None:
    assert resolve_when(expression, THURSDAY) == expected
