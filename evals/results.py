"""What a run leaves behind: one JSON file per run in evals/results/,
committed, the way AskDocs keeps its runs (spec §8.4). A run that stopped on a
quota is saved too, unfinished, and resumes from the same file.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from evals.judge import Score

RESULTS = Path(__file__).parent / "results"


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class TurnRecord(BaseModel):
    """One turn's numbers, as agent_runs metered it."""

    duration_ms: int
    model_used: str | None
    models_tried: list[str]
    requests: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    outcome: str
    # Time spent waiting out free-tier rate limits, kept out of latency.
    waited_ms: int = 0


class LineRecord(BaseModel):
    speaker: Literal["customer", "assistant"]
    text: str


class ScenarioResult(BaseModel):
    id: str
    tags: list[str]
    mode: str
    status: Literal["pass", "fail", "skipped"]
    # Why it was skipped: a day it names is closed, or out of the booking window.
    reason: str = ""
    # When it ran, in the business's time: what "yarın" or "cumartesi" meant then.
    started_at: datetime | None = None
    # The bookings it expected, resolved against that moment.
    expected: list[str] = []
    # The customer and the conversation the agent's store keeps it under.
    customer: str | None = None
    conversation_id: int | None = None
    checks: list[CheckResult] = []
    dates_right: bool | None = None
    customer_turns: int = 0
    turns: list[TurnRecord] = []
    judge: Score | None = None
    transcript: list[LineRecord] = []

    def check(self, name: str) -> bool | None:
        return next((check.passed for check in self.checks if check.name == name), None)


class RunConfig(BaseModel):
    agent_models: list[str]
    simulator_model: str
    judge_model: str
    commit: str


type Suite = Literal["smoke", "full"]


class RunResult(BaseModel):
    version: int = 1
    id: str
    suite: Suite
    # The scenarios --only named; None for the whole suite. A resume keeps them.
    only: list[str] | None = None
    # Runs of each scenario: with more than one, `scenarios` holds them all,
    # and a scenario counts as passed only if it passed every one.
    trials: int = 1
    started_at: datetime
    finished_at: datetime | None = None
    config: RunConfig
    scenarios: list[ScenarioResult] = []
    # The smoke scenarios again, at the end of a full run: the noise band.
    repeats: list[ScenarioResult] = []
    # Why the run stopped short. It resumes with --resume <id>.
    stopped: str | None = None

    @property
    def complete(self) -> bool:
        return self.finished_at is not None


def save(run: RunResult, directory: Path = RESULTS) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.id}.json"
    path.write_text(run.model_dump_json(indent=1) + "\n", encoding="utf-8")
    return path


def load(run_id: str, directory: Path = RESULTS) -> RunResult:
    return RunResult.model_validate_json((directory / f"{run_id}.json").read_text(encoding="utf-8"))


def history(directory: Path = RESULTS) -> list[RunResult]:
    """Every finished run, oldest first."""
    runs = [
        RunResult.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]
    return sorted((run for run in runs if run.complete), key=lambda run: run.started_at)
