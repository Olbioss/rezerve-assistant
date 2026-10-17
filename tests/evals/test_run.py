import pytest

from evals.results import ScenarioResult
from evals.run import parse_args, pending, select
from evals.scenario import Expect, Scenario


def scenario(id: str, *, smoke: bool = False) -> Scenario:
    return Scenario(
        id=id, tags=["faq"], mode="scripted", persona="p", turns=["?"], smoke=smoke, expect=Expect()
    )


SUITE = [scenario("a", smoke=True), scenario("b"), scenario("c", smoke=True)]


def test_a_suite_is_the_smoke_subset_or_everything() -> None:
    assert [s.id for s in select(SUITE, "smoke", None)] == ["a", "c"]
    assert [s.id for s in select(SUITE, "full", None)] == ["a", "b", "c"]
    # --only picks from all of them, smoke or not.
    assert [s.id for s in select(SUITE, "smoke", ["b"])] == ["b"]


def test_an_unknown_scenario_is_refused_before_anything_runs() -> None:
    with pytest.raises(SystemExit, match="Unknown scenarios: nope"):
        select(SUITE, "full", ["nope"])


def test_the_command_line() -> None:
    args = parse_args(["--suite", "full", "--agent-model", "groq:openai/gpt-oss-120b"])
    assert (args.suite, args.agent_model, args.only, args.resume) == (
        "full",
        "groq:openai/gpt-oss-120b",
        None,
        None,
    )
    assert parse_args([]).suite == "smoke"


def test_a_resumed_run_runs_only_what_never_got_a_result() -> None:
    done = [ScenarioResult(id="a", tags=["faq"], mode="scripted", status="fail")]
    assert [s.id for s in pending(done, SUITE)] == ["b", "c"]
