"""Turkish day expressions to dates, by rule rather than by the model's
arithmetic (spec §8.6): "haftaya salı", "ayın 17'si", "cumartesi öğleden
sonra". Built because the evals showed the calendar block alone was not
enough; the model calls it as the `resolve_when` tool.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from assistant.agent.format import MONTHS, WEEKDAYS

# Longest first, so "öğleden sonra" is not read as "öğle", nor "akşamüstü" as "akşam".
DAY_PARTS: dict[str, tuple[str, str]] = {
    "öğleden sonra": ("14:00", "18:00"),
    "öğleden önce": ("09:00", "12:00"),
    "akşamüstü": ("16:00", "19:00"),
    "akşam üstü": ("16:00", "19:00"),
    "sabah": ("09:00", "12:00"),
    "öğle": ("12:00", "14:00"),
    "akşam": ("18:00", "21:00"),
}
_WEEKDAYS = tuple(day.lower() for day in WEEKDAYS)
# "pazartesi" before "pazar" and "cumartesi" before "cuma": each starts with the other.
_DAY_NAMES = ("pazartesi", "cumartesi", "salı", "çarşamba", "perşembe", "cuma", "pazar")
_MONTH = re.compile(r"\b(\d{1,2}) (" + "|".join(month.lower() for month in MONTHS) + r")")
_DAY_OF_MONTH = re.compile(r"\bayın (\d{1,2})")


@dataclass(frozen=True)
class When:
    day: date
    weekday: str
    # The part of the day named, if any: "14:00"–"18:00" for "öğleden sonra".
    earliest: str | None = None
    latest: str | None = None


def _normalise(text: str) -> str:
    lowered = text.replace("I", "ı").replace("İ", "i").lower()
    lowered = lowered.replace("'", "").replace("’", "")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in lowered).split())


def resolve_when(expression: str, today: date) -> When | None:
    """The date `expression` names, seen from `today`; None if it names none."""
    text = _normalise(expression)
    span: tuple[str, str] | None = None
    for phrase, hours in DAY_PARTS.items():
        if phrase in text:
            span = hours
            text = text.replace(phrase, " ")
            break
    day = _day(text, today)
    if day is None:
        return None
    earliest, latest = span or (None, None)
    return When(day, WEEKDAYS[day.weekday()], earliest, latest)


def _day(text: str, today: date) -> date | None:
    words = text.split()
    if "bugün" in words:
        return today
    if any(word.startswith("yarın") for word in words):
        return today + timedelta(days=1)
    if "öbür gün" in text or "öbürgün" in text or "ertesi gün" in text:
        return today + timedelta(days=2)
    if match := _MONTH.search(text):
        month = [m.lower() for m in MONTHS].index(match[2]) + 1
        day = _safe_date(today.year, month, int(match[1]))
        if day is not None and day < today:
            day = _safe_date(today.year + 1, month, int(match[1]))
        return day
    if match := _DAY_OF_MONTH.search(text):
        day = _safe_date(today.year, today.month, int(match[1]))
        if day is None or day < today:
            year, month = (
                (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            )
            day = _safe_date(year, month, int(match[1]))
        return day
    if "hafta sonu" in text or "haftasonu" in text:
        return today + timedelta(days=(5 - today.weekday()) % 7)
    name = next((n for word in words for n in _DAY_NAMES if word.startswith(n)), None)
    if name is None:
        return None
    weekday = _WEEKDAYS.index(name)
    if "haftaya" in words or "gelecek hafta" in text or "önümüzdeki hafta" in text:
        monday = today - timedelta(days=today.weekday())
        return monday + timedelta(days=7 + weekday)
    # "bu cuma", or a weekday alone: the nearest one from today on.
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
