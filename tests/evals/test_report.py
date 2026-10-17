from datetime import UTC, datetime, timedelta

import pytest

from evals.judge import Score
from evals.report import noise_band, previous, price, render, summarize
from evals.results import CheckResult, RunConfig, RunResult, ScenarioResult, TurnRecord

CONFIG = RunConfig(
    agent_models=["google:gemini-3.5-flash-lite"],
    simulator_model="groq:openai/gpt-oss-120b",
    judge_model="groq:openai/gpt-oss-120b",
    commit="abc1234",
)


def turn(
    ms: int, *, tried: tuple[str, ...] = ("gemini-3.5-flash-lite",), **numbers: int
) -> TurnRecord:
    values = {"requests": 1, "input_tokens": 1_000, "output_tokens": 100, "tool_calls": 1}
    return TurnRecord(
        duration_ms=ms,
        model_used=tried[-1],
        models_tried=list(tried),
        tool_errors=numbers.pop("tool_errors", 0),
        outcome="reply",
        **(values | numbers),
    )


def scenario(id: str, status: str, *tags: str, **fields: object) -> ScenarioResult:
    checks = fields.pop("checks", [CheckResult(name="nothing_else", passed=status == "pass")])
    return ScenarioResult(
        id=id,
        tags=list(tags),
        mode="scripted",
        status=status,  # pyright: ignore[reportArgumentType]
        checks=checks,  # pyright: ignore[reportArgumentType]
        customer_turns=2,
        **fields,  # pyright: ignore[reportArgumentType]
    )


def run(id: str, scenarios: list[ScenarioResult], **fields: object) -> RunResult:
    started = datetime(2026, 10, 16, tzinfo=UTC) + timedelta(hours=int(id))
    return RunResult(
        id=id,
        suite="full",
        started_at=started,
        finished_at=started + timedelta(minutes=30),
        config=CONFIG,
        scenarios=scenarios,
        **fields,  # pyright: ignore[reportArgumentType]
    )


SCENARIOS = [
    scenario("book", "pass", "booking", "date", dates_right=True, turns=[turn(800), turn(1200)]),
    scenario(
        "move",
        "fail",
        "reschedule",
        "date",
        dates_right=False,
        turns=[turn(2000, tried=("gemini-3.5-flash-lite", "openai/gpt-oss-120b"), tool_errors=1)],
        checks=[CheckResult(name="moved", passed=False, detail="m1")],
    ),
    scenario(
        "trick",
        "pass",
        "injection",
        "judge",
        turns=[turn(500)],
        judge=Score(fluency=4, politeness=5, clarity=3, note=""),
    ),
    scenario("sunday", "skipped", "booking", reason="the business is closed"),
]


def test_a_run_is_summed_up_over_the_scenarios_that_ran() -> None:
    summary = summarize(SCENARIOS)
    assert str(summary.success) == "67% (2/3)"
    assert {tag: str(rate) for tag, rate in summary.by_tag.items()} == {
        "booking": "100% (1/1)",
        "reschedule": "0% (0/1)",
        "date": "50% (1/2)",
        "injection": "100% (1/1)",
        "judge": "100% (1/1)",
    }
    assert str(summary.date_resolution) == "50% (1/2)"
    assert str(summary.injection_safe) == "100% (1/1)"
    assert summary.skipped == 1
    assert (summary.input_tokens, summary.output_tokens) == (4_000, 400)
    assert summary.latency_p50_ms == 1_000
    assert str(summary.fallback_rate) == "25% (1/4)"
    assert str(summary.tool_error_rate) == "25% (1/4)"
    assert summary.judge == {"fluency": 4, "politeness": 5, "clarity": 3}
    # 3,000 tokens in and 300 out on Gemini, 1,000 and 100 on Groq.
    assert summary.cost_usd == pytest.approx(
        (3_000 * 0.30 + 300 * 2.50 + 1_000 * 0.15 + 100 * 0.60) / 1_000_000
    )


def test_a_model_is_priced_by_the_longest_name_it_starts_with() -> None:
    assert price("gemini-3.5-flash-lite-001") == (0.30, 2.50)
    assert price("models/gemini-2.5-flash-lite") == (0.10, 0.40)
    assert price("some-new-model") is None


def test_the_noise_band_is_how_often_a_rerun_changes_the_result() -> None:
    first = [scenario(id, "pass", "booking") for id in ("a", "b", "c", "d")]
    repeats = [scenario("a", "fail", "booking"), *first[1:]]
    assert noise_band(run("1", first, repeats=repeats)) == 0.25
    assert noise_band(run("1", first)) is None


def test_the_report_shows_targets_failures_and_what_changed() -> None:
    before = run("1", [scenario("book", "fail", "booking"), scenario("move", "pass", "reschedule")])
    now = run("2", SCENARIOS)
    assert previous([before, now], now) == before
    report = render(now, [before, now])
    assert "| Task success | 67% (2/3) | ≥ 90% | ✗ |" in report
    assert "| Writes without an approval | 0 | 0 | ✓ |" in report
    assert "- `move`: moved (m1)" in report
    assert "## Since 1" in report
    assert "- Task success on the 2 scenarios both ran: 50% (1/2) → 50% (1/2) (+0%)" in report
    assert "- `book` fail → pass" in report


def test_a_run_where_everything_was_skipped_still_reports() -> None:
    skipped = [scenario("sunday", "skipped", "booking", reason="closed")]
    assert str(summarize(skipped).success) == "–"
    assert "| Task success | – | ≥ 90% | – |" in render(run("1", skipped), [])
