"""Run the evals (spec §8.5):

    uv run python -m evals.run --suite smoke|full [--only <id> ...]
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
from evals.results import RunConfig, RunResult, ScenarioResult, history, load, save
from evals.rezerve_side import RezerveDb
from evals.scenario import Scenario, load_scenarios
from evals.simulate import Simulator


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    parser.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--only", nargs="+", metavar="ID", help="run just these scenarios")
    parser.add_argument("--agent-model", help="one model for the agent, no fallback")
    parser.add_argument("--resume", metavar="RUN_ID", help="carry on a run a quota stopped")
    return parser.parse_args(argv)


def select(scenarios: list[Scenario], suite: str, only: list[str] | None) -> list[Scenario]:
    chosen = [s for s in scenarios if suite == "full" or s.smoke]
    if only:
        unknown = set(only) - {s.id for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenarios: {', '.join(sorted(unknown))}")
        chosen = [s for s in scenarios if s.id in only]
    return chosen


def pending(done: list[ScenarioResult], todo: list[Scenario]) -> list[Scenario]:
    """What is left to run. A scenario a quota cut off never got a result, so
    it runs again from the start, Rezerve reset and all."""
    finished = {result.id for result in done}
    return [scenario for scenario in todo if scenario.id not in finished]


def settings_for(args: argparse.Namespace) -> EvalSettings:
    settings = EvalSettings()  # pyright: ignore[reportCallIssue]
    if args.agent_model:
        settings = settings.model_copy(
            update={"model_primary": args.agent_model, "model_fallbacks": []}
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


async def execute(args: argparse.Namespace, settings: EvalSettings) -> int:
    scenarios = select(load_scenarios(), args.suite, args.only)
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

        if args.resume:
            run = load(args.resume)
        else:
            started = datetime.now(UTC)
            run = RunResult(
                id=f"{started:%Y-%m-%dT%H-%M-%SZ}-{args.suite}",
                suite=args.suite,
                started_at=started,
                config=RunConfig(
                    agent_models=[settings.model_primary, *settings.model_fallbacks],
                    simulator_model=settings.simulator_model,
                    judge_model=settings.judge_model,
                    commit=commit(),
                ),
            )
        run.stopped = None
        repeats = [s for s in scenarios if s.smoke] if args.suite == "full" else []
        try:
            for results, todo, attempt in (
                (run.scenarios, scenarios, "1"),
                (run.repeats, repeats, "2"),
            ):
                for scenario in pending(results, todo):
                    result = await run_scenario(scenario, rig, f"{run.id}.{attempt}")
                    results.append(result)
                    save(run)
                    print(f"{result.status:7} {scenario.id}", flush=True)
        except QuotaExhausted as error:
            run.stopped = str(error)
            path = save(run)
            print(f"Stopped: {error}\nSaved {path}. Resume with: --resume {run.id}")
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
    settings = settings_for(args)
    asyncio.run(ensure_database(settings.eval_database_url))
    migrate(settings.eval_database_url)
    return asyncio.run(execute(args, settings))


if __name__ == "__main__":
    sys.exit(main())
