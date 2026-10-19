"""Days the way scenarios name them, resolved the way Rezerve's slot engine would.

A scenario says when a booking should land in words that stay true on any day
the suite runs: "next saturday", "tuesday next week", "in 9 days". Resolved in
the business timezone, "next saturday" is the nearest Saturday on which the
time is still bookable after the minimum lead time, so it may be today
(spec §8.3); "next same weekday" is the same for today's weekday. "<weekday>
next week" is that day of the next Monday-to-Sunday week, the way "haftaya
salı" is meant.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from assistant.agent.format import MONTHS
from assistant.agent.format import WEEKDAYS as WEEKDAY_NAMES

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NEXT = re.compile(rf"next ({'|'.join(WEEKDAYS)})")
_NEXT_WEEK = re.compile(rf"({'|'.join(WEEKDAYS)}) next week")
_IN_DAYS = re.compile(r"in (\d+) days?")


class UnknownDay(ValueError):
    """An expression this module does not know: a mistake in a scenario file."""


@dataclass(frozen=True)
class Clock:
    """The moment a scenario starts, as the business sees it."""

    now: datetime
    timezone: str
    min_lead: timedelta

    @classmethod
    def at(cls, instant: datetime, timezone: str, min_lead: timedelta) -> "Clock":
        return cls(instant.astimezone(ZoneInfo(timezone)), timezone, min_lead)

    @property
    def today(self) -> date:
        return self.now.date()


def parse_time(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


def resolve_day(expression: str, clock: Clock, at: time | None = None) -> date:
    """The date `expression` names; `at` lets "next <weekday>" skip a time already gone."""
    words = " ".join(expression.lower().split())
    today = clock.today
    if words == "next same weekday":
        words = f"next {WEEKDAYS[today.weekday()]}"
    if words == "today":
        return today
    if words == "tomorrow":
        return today + timedelta(days=1)
    if words == "day after tomorrow":
        return today + timedelta(days=2)
    if match := _IN_DAYS.fullmatch(words):
        return today + timedelta(days=int(match[1]))
    if match := _NEXT.fullmatch(words):
        day = today + timedelta(days=(WEEKDAYS.index(match[1]) - today.weekday()) % 7)
        if at is not None and combine(day, at, clock) < clock.now + clock.min_lead:
            day += timedelta(days=7)
        return day
    if match := _NEXT_WEEK.fullmatch(words):
        monday = today - timedelta(days=today.weekday())
        return monday + timedelta(days=7 + WEEKDAYS.index(match[1]))
    raise UnknownDay(
        f"Unknown day {expression!r}: use today, tomorrow, day after tomorrow, "
        "in N days, next <weekday>, next same weekday or <weekday> next week."
    )


def combine(day: date, at: time, clock: Clock) -> datetime:
    return datetime.combine(day, at, tzinfo=ZoneInfo(clock.timezone))


def resolve(expression: str, at: str, clock: Clock) -> datetime:
    """The instant `expression` at `at` names, in the business timezone."""
    moment = parse_time(at)
    return combine(resolve_day(expression, clock, moment), moment, clock)


def day_label(day: date) -> str:
    """A date the way a customer writes it: "28 Ekim"."""
    return f"{day.day} {MONTHS[day.month - 1]}"


def weekday_label(day: date) -> str:
    """A weekday the way a customer types it: "perşembe"."""
    return WEEKDAY_NAMES[day.weekday()].lower()


_TEMPLATE = re.compile(r"\{(date|weekday):([^}]+)\}")


def render(text: str, clock: Clock) -> str:
    """A scripted line with its `{date:<expression>}` and `{weekday:<expression>}`
    filled in for today's run."""

    def fill(match: re.Match[str]) -> str:
        day = resolve_day(match[2], clock)
        return day_label(day) if match[1] == "date" else weekday_label(day)

    return _TEMPLATE.sub(fill, text)
