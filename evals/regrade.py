"""Grade saved runs again from their transcripts (spec §8.3):

    uv run python -m evals.regrade <run-id> [<run-id> ...]

The phrase, amount and privacy checks read only what the assistant said, so
when one of them is corrected a saved run is graded again from its
transcripts: no scenario runs and no model is called. What the other checks
read from Rezerve's database is kept as it was. Needs the eval business's
database, for its prices.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import asdict

from evals.config import EvalSettings
from evals.graders import transcript_checks
from evals.harness import allowed_amounts, private_details
from evals.report import render
from evals.results import CheckResult, ScenarioResult, history, load, save
from evals.rezerve_side import RezerveDb
from evals.scenario import Scenario, load_scenarios


def regrade(result: ScenarioResult, scenario: Scenario, amounts: set[int]) -> ScenarioResult:
    """`result` with its transcript checks graded again, every other check kept."""
    if result.status == "skipped":
        return result
    said = [line.text for line in result.transcript if line.speaker == "assistant"]
    expect = scenario.expect
    fresh = {
        check.name: CheckResult(**asdict(check))
        for check in transcript_checks(
            said, expect.says, expect.never_says, private_details(scenario), amounts
        )
    }
    checks = [fresh.get(check.name, check) for check in result.checks]
    status = "pass" if all(check.passed for check in checks) else "fail"
    return result.model_copy(update={"checks": checks, "status": status})


async def _amounts(settings: EvalSettings) -> set[int]:
    rezerve = RezerveDb(settings.rezerve_database_url)
    try:
        return allowed_amounts(await rezerve.business())
    finally:
        await rezerve.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regrade", description=__doc__)
    parser.add_argument("runs", nargs="+", metavar="RUN_ID")
    args = parser.parse_args(argv)
    scenarios = {scenario.id: scenario for scenario in load_scenarios()}
    amounts = asyncio.run(_amounts(EvalSettings()))  # pyright: ignore[reportCallIssue]
    for run_id in args.runs:
        run = load(run_id)
        for results in (run.scenarios, run.repeats):
            for index, result in enumerate(results):
                results[index] = regrade(result, scenarios[result.id], amounts)
                if results[index].status != result.status:
                    print(f"{run_id}: {result.id} {result.status} → {results[index].status}")
        path = save(run)
        if run.complete:
            path.with_suffix(".md").write_text(render(run, history()), encoding="utf-8")
        print(f"Graded again: {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
