"""Turkish day expressions to dates, by rule rather than by the model's
arithmetic (spec §8.6): "haftaya salı", "ayın 17'si", "cumartesi öğleden
sonra". Built because the evals showed the calendar block alone was not
enough; the model calls it as the `resolve_when` tool.

Customers type without Turkish letters ("yarin", "haftaya sali"), so the
expression and every word below are compared with those letters folded. An
expression naming two different days ("yarın değil, haftaya salı") names
none: the model asks.
"""

import re
from dataclasses import dataclass
from datetime import date, time, timedelta

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
_TURKISH = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _fold(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower().translate(_TURKISH)


_DAY_PARTS = {_fold(phrase): hours for phrase, hours in DAY_PARTS.items()}
_WEEKDAYS = [_fold(day) for day in WEEKDAYS]
_MONTHS = [_fold(month) for month in MONTHS]
# A weekday as it is written in a sentence: "salı", "salıya", "cumadan", "perşembeleri".
# Anything else that starts with one is another word: "Salim", "pazarlık".
_SUFFIXES = {"", "ya", "ye", "a", "e", "da", "de", "ta", "te", "dan", "den", "tan", "ten",
             "yi", "i", "nin", "si", "ki", "leri", "lari"}  # fmt: skip
_MONTH = re.compile(r"\b(\d{1,2}) (" + "|".join(_MONTHS) + r")")
_DAY_OF_MONTH = re.compile(r"\bayin (\d{1,2})")
# "28.10", "28/10/2026"; not a time: "saat 12.10".
_FIGURES = re.compile(r"(?<![\d:.])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?![\d:])")
_NEXT_WEEK = ("haftaya", "gelecek hafta", "onumuzdeki hafta")
# A day of the year that passed this recently was a slip, not a wish for next year.
_JUST_PASSED = timedelta(days=183)


@dataclass(frozen=True)
class When:
    day: date
    weekday: str
    # The part of the day named, if any: "14:00"–"18:00" for "öğleden sonra".
    earliest: str | None = None
    latest: str | None = None
    # Today's own weekday named alone ("perşembe" on a Thursday): the same day
    # next week, which is what it means once the hour asked for has gone.
    or_next_week: date | None = None
    # The hour named had gone today, so `day` is already a week today.
    hour_gone: bool = False


def _normalise(text: str) -> str:
    folded = _fold(text).replace("'", "").replace("’", "")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def resolve_when(expression: str, today: date, soonest: time | None = None) -> When | None:
    """The date `expression` names, seen from `today`; None if it names none,
    or more than one. `soonest` is the earliest time still bookable today: an
    hour before it, on today's own weekday named alone, means a week today."""
    text = _normalise(expression)
    span: tuple[str, str] | None = None
    for phrase, hours in _DAY_PARTS.items():
        if phrase in text:
            span = hours
            text = text.replace(phrase, " ")
            break
    days = set(_days(text, today)) or set(_figures(expression, today))
    if len(days) != 1:
        return None
    [day] = days
    if day is None:
        return None
    earliest, latest = span or (None, None)
    if not (day == today and _weekday_alone(text, today)):
        return When(day, WEEKDAYS[day.weekday()], earliest, latest)
    hour = _clock(expression)
    if soonest is not None and hour is not None:
        if hour < soonest:
            later = day + timedelta(days=7)
            return When(later, WEEKDAYS[later.weekday()], earliest, latest, hour_gone=True)
        # The hour is still ahead today: today, and nothing else to choose from.
        return When(day, WEEKDAYS[day.weekday()], earliest, latest)
    return When(day, WEEKDAYS[day.weekday()], earliest, latest, day + timedelta(days=7))


_CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _clock(expression: str) -> time | None:
    """The first hour written as "10:00" in `expression`."""
    match = _CLOCK.search(expression)
    return time(int(match[1]), int(match[2])) if match else None


def _weekday_alone(text: str, today: date) -> bool:
    """Today's own weekday named without "bu" before it, and without "bugün"."""
    words = text.split()
    if "bugun" in words:
        return False
    return any(
        _weekday(word) == today.weekday() and (i == 0 or words[i - 1] != "bu")
        for i, word in enumerate(words)
    )


def _figures(expression: str, today: date) -> list[date | None]:
    """Dates written in figures, read only when no word names a day: in
    "yarın 15.30" the figures are a time."""
    found: list[date | None] = []
    for match in _FIGURES.finditer(expression):
        if expression[: match.start()].rstrip().lower().endswith("saat"):
            continue
        day, month, year = int(match[1]), int(match[2]), match[3]
        if _safe_date(today.year, month, day) is None:
            continue
        if year is None:
            found.append(_coming(day, month, today))
        else:
            named = _safe_date(int(year), month, day)
            found.append(named if named is not None and named >= today else None)
    return found


def _days(text: str, today: date) -> list[date | None]:
    """Every day `text` names in words; None for one that has just passed."""
    found: list[date | None] = []
    next_week = any(marker in text for marker in _NEXT_WEEK)
    monday = today - timedelta(days=today.weekday())
    if "yarindan sonra" in text or "obur gun" in text or "oburgun" in text:
        found.append(today + timedelta(days=2))
        text = text.replace("yarindan sonra", " ")
    words = text.split()
    if "bugun" in words:
        found.append(today + timedelta(days=7) if "haftaya bugun" in text else today)
    if any(word.startswith("yarin") for word in words):
        found.append(today + timedelta(days=1))
    for match in _MONTH.finditer(text):
        found.append(_coming(int(match[1]), _MONTHS.index(match[2]) + 1, today))
    for match in _DAY_OF_MONTH.finditer(text):
        day = _safe_date(today.year, today.month, int(match[1]))
        if day is None or day < today:
            year, month = (
                (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            )
            day = _safe_date(year, month, int(match[1]))
        found.append(day)
    for word in words:
        weekday = _weekday(word)
        if weekday is None:
            continue
        if next_week:
            found.append(monday + timedelta(days=7 + weekday))
        else:
            # "bu cuma", or a weekday alone: the nearest one from today on.
            found.append(today + timedelta(days=(weekday - today.weekday()) % 7))
    if not found and ("hafta sonu" in text or "haftasonu" in text):
        # The weekend's Saturday, unless a day was named: "hafta sonu, haftaya cumartesi".
        if next_week:
            found.append(monday + timedelta(days=12))
        else:
            found.append(today + timedelta(days=(5 - today.weekday()) % 7))
    return found


def _weekday(word: str) -> int | None:
    # Longest first: "pazartesi" starts with "pazar", "cumartesi" with "cuma".
    for name in sorted(_WEEKDAYS, key=len, reverse=True):
        if word.startswith(name) and word[len(name) :] in _SUFFIXES:
            return _WEEKDAYS.index(name)
    return None


def _coming(day: int, month: int, today: date) -> date | None:
    """The next `day` `month` from today on; None for one that has just passed."""
    named = _safe_date(today.year, month, day)
    if named is None or named >= today:
        return named
    if today - named < _JUST_PASSED:
        return None
    return _safe_date(today.year + 1, month, day)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
