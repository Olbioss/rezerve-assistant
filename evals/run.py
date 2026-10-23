"""Run the evals (spec §8.5):

    uv run python -m evals.run --suite smoke|full [--only <id> ...] [--trials <n>]
        [--agent-model <provider:model>] [--resume <run-id>]

Needs Rezerve running locally on the Compose database with the eval business
seeded (`bun run seed:eval`), the same ASSISTANT_MCP_TOKEN on both sides, and
EMAIL_DELIVERY=off — see the README. A daily quota stops the run with its
results saved; `--resume <run-id>` carries on after the reset.
"""

import argparse
import asyncio
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from assistant.agent.agent import build_agent
from assistant.agent.approvals import ReplyClassifier
from assistant.conversation.store import Store
from assistant.conversation.turn import TurnProcessor, TurnSettings
from assistant.db.session import create_engine, session_factory
from assistant.mcp import connect
from evals.cache import DiskCache
from evals.config import EvalSettings
from evals.harness import EVAL_SLUG, Outbox, Rig, run_scenario
from evals.judge import Judge
from evals.models import QuotaExhausted, patient_chain
from evals.report import render
from evals.results import RunConfig, RunResult, ScenarioResult, Suite, history, load, save
from evals.rezerve_side import RezerveDb
from evals.scenario import Scenario, load_scenarios
from evals.simulate import Simulator


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    parser.add_argument("--suite", choices=["smoke", "full"], help="smoke unless resuming")
    parser.add_argument("--only", nargs="+", metavar="ID", help="run just these scenarios")
    parser.add_argument(
        "--trials",
        type=_at_least_one,
        metavar="N",
        help="run each scenario N times; it passes only if it passes each time (default 1)",
    )
    parser.add_argument("--agent-model", help="one model for the agent, no fallback")
    parser.add_argument(
        "--resume", metavar="RUN_ID", help="carry on a run a quota stopped, as it started"
    )
    return parser.parse_args(argv)


def _at_least_one(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def selection(args: argparse.Namespace, run: RunResult | None) -> tuple[Suite, list[str] | None]:
    """The suite and the scenarios: a resumed run's own, or the command line's."""
    if run is not None:
        return run.suite, run.only
    suite: Suite = args.suite or "smoke"
    return suite, args.only


def trials_for(args: argparse.Namespace, run: RunResult | None) -> int:
    """Runs of each scenario: a resumed run's own, or the command line's."""
    if run is not None:
        return run.trials
    return args.trials or 1


def agent_models(args: argparse.Namespace, run: RunResult | None) -> list[str] | None:
    """The agent's models when they are not the configured chain: a resumed
    run's own, or the one --agent-model names."""
    if run is not None:
        return run.config.agent_models
    return [args.agent_model] if args.agent_model else None


def check_resume(args: argparse.Namespace, run: RunResult) -> None:
    """Refuse a flag given with --resume that disagrees with how the run
    started: its results would be measured on something else."""
    clashes: list[str] = []
    if args.suite and args.suite != run.suite:
        clashes.append(f"--suite {args.suite} (it is a {run.suite} run)")
    if args.only and args.only != run.only:
        clashes.append(f"--only (it ran {' '.join(run.only) if run.only else 'the whole suite'})")
    if args.trials and args.trials != run.trials:
        clashes.append(f"--trials {args.trials} (it runs each scenario {run.trials} times)")
    if args.agent_model and [args.agent_model] != run.config.agent_models:
        clashes.append(f"--agent-model (it ran {' → '.join(run.config.agent_models)})")
    if clashes:
        raise SystemExit(f"{run.id} carries on as it started; drop " + "; ".join(clashes))


def select(scenarios: list[Scenario], suite: str, only: list[str] | None) -> list[Scenario]:
    chosen = [s for s in scenarios if suite == "full" or s.smoke]
    if only:
        unknown = set(only) - {s.id for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenarios: {', '.join(sorted(unknown))}")
        chosen = [s for s in scenarios if s.id in only]
    return chosen


def pending(
    done: list[ScenarioResult], todo: list[Scenario], trials: int = 1
) -> list[tuple[Scenario, int]]:
    """What is left to run, trial by trial: every scenario's first run, then
    every second one. A scenario a quota cut off never got a result, so it
    runs again from the start, Rezerve reset and all."""
    finished = Counter(result.id for result in done)
    return [
        (scenario, trial)
        for trial in range(1, trials + 1)
        for scenario in todo
        if finished[scenario.id] < trial
    ]


def settings_for(models: list[str] | None) -> EvalSettings:
    settings = EvalSettings()  # pyright: ignore[reportCallIssue]
    if models:
        settings = settings.model_copy(
            update={"model_primary": models[0], "model_fallbacks": models[1:]}
        )
    return settings


async def ensure_database(url: str) -> None:
    """Create the eval database on the Compose server if it is not there yet."""
    target = make_url(url)
    server = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with server.connect() as connection:
            exists = await connection.scalar(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                await connection.execute(sa.text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await server.dispose()


def migrate(url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def execute(args: argparse.Namespace, settings: EvalSettings, run: RunResult | None) -> int:
    suite, only = selection(args, run)
    scenarios = select(load_scenarios(), suite, only)
    engine = create_engine(settings.eval_database_url)
    rezerve = RezerveDb(settings.rezerve_database_url)
    cache = DiskCache()
    try:
        store = Store(session_factory(engine))
        agent_model, agent_patience = patient_chain(settings)
        simulator_model, simulator_patience = patient_chain(
            settings.model_copy(
                update={"model_primary": settings.simulator_model, "model_fallbacks": []}
            )
        )
        judge_model, judge_patience = patient_chain(
            settings.model_copy(
                update={"model_primary": settings.judge_model, "model_fallbacks": []}
            )
        )
        outbox = Outbox()
        token = settings.assistant_mcp_token.get_secret_value()
        rig = Rig(
            store=store,
            processor=TurnProcessor(
                store=store,
                agent=build_agent(agent_model),
                classifier=ReplyClassifier(agent_model),
                open_rezerve=partial(connect, settings.rezerve_mcp_url, token),
                channels={"web": outbox},
                settings=TurnSettings.from_settings(settings),
            ),
            outbox=outbox,
            rezerve=rezerve,
            secret=settings.customer_ref_secret.get_secret_value(),
            simulator=Simulator(simulator_model, cache),
            judge=Judge(judge_model, cache),
            patience=agent_patience + simulator_patience + judge_patience,
        )
        await preflight(settings, rezerve, token)

        if run is None:
            started = datetime.now(UTC)
            run = RunResult(
                id=f"{started:%Y-%m-%dT%H-%M-%SZ}-{suite}",
                suite=suite,
                only=only,
                trials=trials_for(args, None),
                started_at=started,
                config=RunConfig(
                    agent_models=[settings.model_primary, *settings.model_fallbacks],
                    simulator_model=settings.simulator_model,
                    judge_model=settings.judge_model,
                    commit=commit(),
                ),
            )
        run.stopped = None
        # A full run's smoke scenarios run once more for the noise band,
        # unless every scenario already runs several times.
        once = suite == "full" and run.trials == 1
        repeats = [s for s in scenarios if s.smoke] if once else []
        try:
            for results, todo, trials, again in (
                (run.scenarios, scenarios, run.trials, False),
                (run.repeats, repeats, 1, True),
            ):
                for scenario, trial in pending(results, todo, trials):
                    attempt = "2" if again else str(trial)
                    result = await run_scenario(
                        scenario, rig, f"{run.id}.{attempt}", trial=1 if again else trial
                    )
                    results.append(result)
                    save(run)
                    which = f" (run {trial} of {trials})" if trials > 1 else ""
                    print(f"{result.status:7} {scenario.id}{which}", flush=True)
        except QuotaExhausted as error:
            run.stopped = str(error)
            path = save(run)
            print(
                f"Stopped: {error}\nSaved {path}. Resume with: "
                f"uv run python -m evals.run --resume {run.id}"
            )
            return 2
        run.finished_at = datetime.now(UTC)
        path = save(run)
        report = render(run, history())
        path.with_suffix(".md").write_text(report, encoding="utf-8")
        print(report)
        print(f"Saved {path} and {path.with_suffix('.md').name}")
        return 0
    finally:
        await rezerve.aclose()
        await engine.dispose()


async def preflight(settings: EvalSettings, rezerve: RezerveDb, token: str) -> None:
    """Fail now, not thirty scenarios in: the eval business and Rezerve's MCP."""
    business = await rezerve.business()
    if not business.services:
        raise SystemExit("The eval business has no services: run `bun run seed:eval` in Rezerve.")
    try:
        async with connect(
            settings.rezerve_mcp_url, token, EVAL_SLUG, "eval:" + "a" * 26
        ) as session:
            await session.business_info()
    except Exception as error:
        raise SystemExit(
            f"Rezerve's MCP at {settings.rezerve_mcp_url} did not answer for the eval business "
            f"({error!r}). Is Rezerve running, with the same ASSISTANT_MCP_TOKEN?"
        ) from error


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run = load(args.resume) if args.resume else None
    if run is not None:
        check_resume(args, run)
    settings = settings_for(agent_models(args, run))
    asyncio.run(ensure_database(settings.eval_database_url))
    migrate(settings.eval_database_url)
    return asyncio.run(execute(args, settings, run))


if __name__ == "__main__":
    sys.exit(main())
