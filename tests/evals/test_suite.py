from datetime import UTC, datetime, timedelta

import pytest

from evals.harness import plan
from evals.rezerve_side import Business, Service
from evals.scenario import Scenario, load_scenarios
from evals.when import Clock, render

# The eval business as `bun run seed:eval` makes it.
EVAL_BUSINESS = Business(
    timezone="Europe/Istanbul",
    min_lead=timedelta(minutes=60),
    booking_window_days=30,
    hours={weekday: [(10 * 60, 13 * 60), (14 * 60, 19 * 60)] for weekday in range(6)},
    services={
        "Cilt Bakımı": Service("c", "Cilt Bakımı", 60, 150_000, 30_000),
        "Manikür": Service("m", "Manikür", 30, 50_000, None),
        "Saç Kesimi": Service("s", "Saç Kesimi", 45, 80_000, None),
    },
)
# Monday 12 October 2026, 09:00 in Istanbul: every day a scenario names is open.
MONDAY = Clock.at(datetime(2026, 10, 12, 6, 0, tzinfo=UTC), "Europe/Istanbul", timedelta(hours=1))
SCENARIOS = load_scenarios()


def test_the_suite_covers_every_tag_and_has_a_smoke_subset() -> None:
    tags = {tag for scenario in SCENARIOS for tag in scenario.tags}
    assert tags == {
        "booking", "kapora", "reschedule", "cancel", "date", "faq", "handoff", "injection", "judge",
        "challenge",
    }  # fmt: skip
    assert 25 <= len(SCENARIOS) <= 40
    assert sum(scenario.smoke for scenario in SCENARIOS) >= 5


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.id)
def test_every_scenario_runs_on_an_open_day(scenario: Scenario) -> None:
    # Raises for a time Rezerve would not book, or a service it does not have.
    plan(scenario, EVAL_BUSINESS, MONDAY, "eval:me", "eval:other")
    for line in scenario.turns:
        render(line, MONDAY)
