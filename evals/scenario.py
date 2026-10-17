"""A scenario: one customer, what they write or want, and what Rezerve must
hold afterwards (spec §8.1). One YAML file each, in evals/scenarios/.

Anything a scenario does not expect is expected not to happen: a booking it
does not name, a change to one it seeded, fails it.
"""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIOS = Path(__file__).parent / "scenarios"

type Tag = Literal[
    "booking", "kapora", "reschedule", "cancel", "date", "faq", "handoff", "injection", "judge"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Seed(_Strict):
    """A booking in Rezerve before the customer writes."""

    key: str
    service: str
    day: str
    at: str
    # "me" is this scenario's customer; "other" is someone else entirely.
    customer: Literal["me", "other"] = "me"
    name: str = "Elif Kaya"
    phone: str | None = None
    status: Literal["confirmed", "pending"] = "confirmed"
    paid: bool = False


class When(_Strict):
    """A start time: exactly `at`, or anywhere from `between[0]` to `between[1]`.

    `day` is not `on`, which YAML 1.1 — and so PyYAML — reads as `true`."""

    day: str
    at: str | None = None
    between: tuple[str, str] | None = None

    @model_validator(mode="after")
    def _one_way(self) -> Self:
        if (self.at is None) == (self.between is None):
            raise ValueError("give exactly one of at or between")
        return self


class Booking(When):
    """A booking the customer should end up with."""

    service: str
    status: Literal["confirmed", "pending"] = "confirmed"
    # The name the booking must carry: the one the customer gave, not one made up.
    name: str | None = None


class Move(When):
    """A seeded booking of this customer's, and where it should end up."""

    key: str


class Expect(_Strict):
    bookings: list[Booking] = []
    moved: list[Move] = []
    cancelled: list[str] = []
    handoff: bool = False
    # Phrases some assistant message must contain, and phrases none may.
    says: list[str] = []
    never_says: list[str] = []
    max_turns: int = Field(default=8, ge=1)


class Scenario(_Strict):
    id: str
    tags: list[Tag] = Field(min_length=1)
    # Part of the smoke suite, and of the subset run twice for the noise band.
    smoke: bool = False
    mode: Literal["scripted", "simulated"]
    persona: str
    # scripted: what the customer writes, one line per assistant reply.
    turns: list[str] = []
    # simulated: what the customer wants, and what they say only when asked.
    goal: str | None = None
    facts: dict[str, str] = {}
    # The business's notes for the assistant while this scenario runs.
    business_notes: str | None = None
    seed: list[Seed] = []
    expect: Expect

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.mode == "scripted" and not self.turns:
            raise ValueError("a scripted scenario needs turns")
        if self.mode == "simulated" and not self.goal:
            raise ValueError("a simulated scenario needs a goal")
        if len(self.turns) > self.expect.max_turns:
            raise ValueError("more turns than max_turns")
        keys = [seed.key for seed in self.seed]
        if len(set(keys)) != len(keys):
            raise ValueError("seed keys must be unique")
        mine = {seed.key for seed in self.seed if seed.customer == "me"}
        for key in [move.key for move in self.expect.moved] + self.expect.cancelled:
            if key not in mine:
                raise ValueError(f"{key!r} is not a booking seeded for this customer")
        return self


def load_scenarios(directory: Path = SCENARIOS) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if scenario.id != path.stem:
            raise ValueError(f"{path.name}: id {scenario.id!r} must match the file name")
        scenarios.append(scenario)
    return scenarios
