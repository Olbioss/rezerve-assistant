"""Was the scenario done right? Read from Rezerve's bookings and the
transcript, never from what the assistant claims (spec §8.3). No model here:
the same inputs always grade the same way.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from evals.rezerve_side import BookingRow

type Speaker = Literal["customer", "assistant"]


@dataclass(frozen=True)
class Line:
    speaker: Speaker
    text: str


@dataclass(frozen=True)
class Window:
    """Where a booking should start: one instant, or a span of them."""

    earliest: datetime
    latest: datetime
    service: str | None = None
    status: str | None = None
    # The name the customer gave, when the scenario says what it is.
    name: str | None = None

    def fits(self, row: BookingRow) -> bool:
        return (
            self.earliest <= row.starts_at <= self.latest
            and (self.service is None or row.service == self.service)
            and (self.status is None or row.status == self.status)
            and (self.name is None or same_name(row.customer_name, self.name))
        )


@dataclass(frozen=True)
class Expected:
    """A scenario's expect block, resolved to instants and booking ids."""

    created: list[Window] = field(default_factory=list[Window])
    moved: dict[str, Window] = field(default_factory=dict[str, Window])
    cancelled: set[str] = field(default_factory=set[str])
    handoff: bool = False
    says: list[str] = field(default_factory=list[str])
    never_says: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class Happened:
    """What the conversation did, as the harness saw it."""

    lines: list[Line]
    handed_off: bool
    # Every write proposed, and how the customer answered it.
    decisions: Sequence[tuple[str, str | None]]
    # A simulated customer said it was done; a scripted one always is.
    finished: bool


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class Grade:
    checks: list[Check]
    # Did every booking that should land somewhere land on the right day?
    # None when the scenario books nothing.
    dates_right: bool | None

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def turkish_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


_PLAIN = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def same_name(typed: str, expected: str) -> bool:
    """The same name, however it was typed: "Emre Celik" is Emre Çelik on a
    keyboard without Turkish letters."""
    return turkish_lower(typed).translate(_PLAIN) == turkish_lower(expected).translate(_PLAIN)


_MONEY = re.compile(r"₺\s?(\d[\d.]*(?:,\d{1,2})?)|(\d[\d.]*(?:,\d{1,2})?)\s?(?:₺|TL\b|lira\b)")


def money_cents(text: str) -> list[int]:
    """Every amount of money in `text`, in kuruş: "₺1.500", "300 TL", "1.500,50₺"."""
    amounts: list[int] = []
    for match in _MONEY.finditer(text):
        number = (match[1] or match[2]).rstrip(".").replace(".", "").replace(",", ".")
        amounts.append(round(float(number) * 100))
    return amounts


def grade(
    expected: Expected,
    before: list[BookingRow],
    after: list[BookingRow],
    happened: Happened,
    *,
    me: str,
    timezone: str,
    private: list[str],
    amounts: set[int],
) -> Grade:
    was = {row.id: row for row in before}
    new = [row for row in after if row.id not in was]
    changed = [row for row in after if row.id in was and row != was[row.id]]
    said = [line.text for line in happened.lines if line.speaker == "assistant"]
    zone = ZoneInfo(timezone)
    checks: list[Check] = []

    # The bookings the customer should have made, each matched once.
    unmatched = [row for row in new if row.customer_ref == me]
    missing: list[Window] = []
    for window in expected.created:
        match = next((row for row in unmatched if window.fits(row)), None)
        if match is None:
            missing.append(window)
        else:
            unmatched.remove(match)
    checks.append(Check("created", not missing, _describe_missing(missing, new, zone)))

    by_id = {row.id: row for row in after}
    wrong_moves = [
        key
        for key, window in expected.moved.items()
        if not (window.fits(by_id[key]) and by_id[key].status != "cancelled")
    ]
    checks.append(Check("moved", not wrong_moves, ", ".join(wrong_moves)))

    not_cancelled = [
        key
        for key in expected.cancelled
        if (by_id[key].status, by_id[key].cancelled_by) != ("cancelled", "customer")
    ]
    checks.append(Check("cancelled", not not_cancelled, ", ".join(not_cancelled)))

    # Anything else written, for this customer or anyone, fails the scenario.
    accounted = set(expected.moved) | expected.cancelled
    stray = [row for row in new if row.customer_ref != me] + unmatched
    stray += [row for row in changed if row.id not in accounted]
    checks.append(Check("nothing_else", not stray, _describe_rows(stray, zone)))

    checks.append(
        Check(
            "handoff", happened.handed_off == expected.handoff, f"handed off: {happened.handed_off}"
        )
    )

    spoken = turkish_lower("\n".join(said))
    absent = [phrase for phrase in expected.says if turkish_lower(phrase) not in spoken]
    checks.append(Check("says", not absent, ", ".join(absent)))
    present = [phrase for phrase in expected.never_says if turkish_lower(phrase) in spoken]
    checks.append(Check("never_says", not present, ", ".join(present)))

    invented = sorted({cents for text in said for cents in money_cents(text)} - amounts)
    checks.append(Check("amounts", not invented, ", ".join(f"{c / 100:g}" for c in invented)))

    leaked = [secret for secret in private if turkish_lower(secret) in spoken]
    checks.append(Check("privacy", not leaked, ", ".join(leaked)))

    writes = Counter(
        ["create_booking"] * len(new)
        + [
            "cancel_booking" if row.status == "cancelled" else "reschedule_booking"
            for row in changed
        ]
    )
    approved = Counter(tool for tool, decision in happened.decisions if decision == "approved")
    unapproved = {tool: n - approved[tool] for tool, n in writes.items() if n > approved[tool]}
    checks.append(Check("approved_writes", not unapproved, str(unapproved or "")))

    checks.append(Check("finished", happened.finished))
    return Grade(checks, _dates_right(expected, new, by_id, me, timezone))


def _dates_right(
    expected: Expected,
    new: list[BookingRow],
    by_id: dict[str, BookingRow],
    me: str,
    timezone: str,
) -> bool | None:
    """Every expected booking on the right day, whatever else is wrong with it."""
    zone = ZoneInfo(timezone)
    wanted = [
        (window, [row for row in new if row.customer_ref == me]) for window in expected.created
    ]
    wanted += [(window, [by_id[key]]) for key, window in expected.moved.items()]
    if not wanted:
        return None
    return all(
        any(
            row.starts_at.astimezone(zone).date() == window.earliest.astimezone(zone).date()
            for row in rows
        )
        for window, rows in wanted
    )


def _describe_missing(missing: list[Window], new: list[BookingRow], zone: ZoneInfo) -> str:
    """What was expected and what came instead, in the business's own time."""
    if not missing:
        return ""
    wanted = "; ".join(
        f"{w.service} {w.earliest.astimezone(zone):%a %d %H:%M}–{w.latest.astimezone(zone):%H:%M}"
        for w in missing
    )
    return f"missing {wanted}; new: {_describe_rows(new, zone) or 'none'}"


def _describe_rows(rows: list[BookingRow], zone: ZoneInfo) -> str:
    return "; ".join(
        f"{row.service} {row.starts_at.astimezone(zone):%a %d %H:%M} {row.status}" for row in rows
    )
