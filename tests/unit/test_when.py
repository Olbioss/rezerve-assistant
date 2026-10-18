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
        # Typed without Turkish letters.
        ("yarin", When(date(2026, 10, 16), "Cuma")),
        ("bugun", When(date(2026, 10, 15), "Perşembe")),
        ("haftaya sali", When(date(2026, 10, 20), "Salı")),
        ("persembe ogleden sonra", When(date(2026, 10, 15), "Perşembe", "14:00", "18:00")),
        ("carsambaya", When(date(2026, 10, 21), "Çarşamba")),
        ("obur gun sabah", When(date(2026, 10, 17), "Cumartesi", "09:00", "12:00")),
        # Phrases that start like another one.
        ("yarından sonra", When(date(2026, 10, 17), "Cumartesi")),
        ("yarından sonraki gün sabah", When(date(2026, 10, 17), "Cumartesi", "09:00", "12:00")),
        ("haftaya bugün", When(date(2026, 10, 22), "Perşembe")),
        ("haftaya hafta sonu", When(date(2026, 10, 24), "Cumartesi")),
        (
            "hafta sonu, haftaya cumartesi öğlen",
            When(date(2026, 10, 24), "Cumartesi", "12:00", "14:00"),
        ),
        ("Salim Bey için", None),
        ("pazarlık", None),
        # "The next day" after what? And two days at once: ask.
        ("ertesi gün", None),
        ("yarın değil, haftaya salı", None),
        # Dates in figures; a date that has just passed is a slip, not next year.
        ("28.10", When(date(2026, 10, 28), "Çarşamba")),
        ("28/10 akşam", When(date(2026, 10, 28), "Çarşamba", "18:00", "21:00")),
        ("saat 12.10", None),
        ("yarın 15.30", When(date(2026, 10, 16), "Cuma")),
        ("yarın 12.10'da", When(date(2026, 10, 16), "Cuma")),
        ("14 Ekim", None),
    ],
)
def test_a_turkish_day_is_resolved_by_rule(expression: str, expected: When | None) -> None:
    assert resolve_when(expression, THURSDAY) == expected
