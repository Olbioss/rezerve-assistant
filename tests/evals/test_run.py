from datetime import UTC, datetime

import pytest

from evals.results import RunConfig, RunResult, ScenarioResult
from evals.run import agent_models, check_resume, parse_args, pending, select, selection
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


def test_a_resumed_run_runs_only_what_never_got_a_result() -> None:
    done = [ScenarioResult(id="a", tags=["faq"], mode="scripted", status="fail")]
    assert [s.id for s in pending(done, SUITE)] == ["b", "c"]


GROQ_RUN = RunResult(
    id="2026-10-17T12-51-25Z-full",
    suite="full",
    started_at=datetime(2026, 10, 17, 12, 51, tzinfo=UTC),
    config=RunConfig(
        agent_models=["groq:openai/gpt-oss-120b"],
        simulator_model="groq:openai/gpt-oss-120b",
        judge_model="groq:openai/gpt-oss-120b",
        commit="412ae22",
    ),
)


def test_a_new_run_is_the_smoke_suite_on_the_configured_models_unless_told() -> None:
    assert selection(parse_args([]), None) == ("smoke", None)
    assert agent_models(parse_args([]), None) is None
    told = parse_args(["--suite", "full", "--only", "a", "--agent-model", "groq:m"])
    assert (selection(told, None), agent_models(told, None)) == (("full", ["a"]), ["groq:m"])


def test_a_resumed_run_carries_on_with_what_it_started_with() -> None:
    # The hint a stopped run prints: --resume and nothing else.
    bare = parse_args(["--resume", GROQ_RUN.id])
    assert selection(bare, GROQ_RUN) == ("full", None)
    assert agent_models(bare, GROQ_RUN) == ["groq:openai/gpt-oss-120b"]
    check_resume(bare, GROQ_RUN)
    # Flags that agree with the run are fine.
    check_resume(
        parse_args(["--suite", "full", "--agent-model", "groq:openai/gpt-oss-120b",
                    "--resume", GROQ_RUN.id]),
        GROQ_RUN,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("flags", "refused"),
    [
        (["--suite", "smoke"], "--suite smoke"),
        (["--only", "a"], "--only"),
        (["--agent-model", "google:gemini-3.5-flash-lite"], "--agent-model"),
    ],
)
def test_a_resume_refuses_flags_the_run_did_not_start_with(flags: list[str], refused: str) -> None:
    with pytest.raises(SystemExit, match=refused):
        check_resume(parse_args([*flags, "--resume", GROQ_RUN.id]), GROQ_RUN)
