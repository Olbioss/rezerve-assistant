"""Fourteen dates with their Turkish weekdays, so "haftaya salı" is a
lookup for the model rather than arithmetic it may get wrong."""

from datetime import date, timedelta

from assistant.agent.format import WEEKDAYS

CALENDAR_DAYS = 14


def calendar_block(today: date, days: int = CALENDAR_DAYS) -> str:
    lines: list[str] = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        note = " (bugün)" if offset == 0 else " (yarın)" if offset == 1 else ""
        lines.append(f"{day.isoformat()} {WEEKDAYS[day.weekday()]}{note}")
    return "\n".join(lines)
