# Phase 4a — Production-Ready Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent everything a public deployment needs, built and tested locally with no cloud resource yet. Budgets come first. Then come:
- production configuration;
- Cloud Tasks, with call-backs that carry an OIDC token;
- the Telegram channel, with the owner relay;
- reminders;
- the receiver for Rezerve's "kapora received" event;
- traces to Langfuse;
- CI.

Plan 4b deploys it.

**Architecture:** Everything plugs into the service as it is.
- **Budgets:** the turn checks them before any model call.
- **The queue:** a `TaskQueue` that Cloud Tasks backs in production, with the in-process queue left for development and tests. Cloud Tasks calls the service back at `/internal/*`, behind Google OIDC verification.
- **Telegram:** a channel (Bot API over httpx) and a webhook route.
- **The owner relay and reminders:** both ride on the existing at-least-once outbound machinery. Owner notes are `messages` rows with direction `owner`, and reminders are scheduled after a turn commits.
- **Rezerve's event:** an HMAC-checked endpoint that queues a reply.
- **Traces:** go out over OTLP/HTTP through Logfire's SDK, with nothing sent to Logfire itself.

**Tech Stack:**
- Python 3.13, uv 0.13.1, FastAPI, SQLAlchemy 2.1 async with asyncpg, Alembic, pydantic-settings 2.16, httpx 0.28, Pydantic AI 2.56, pytest.
- New: google-cloud-tasks 2.26, google-auth 2.62, logfire 5.2 with its FastAPI extra (OpenTelemetry's OTLP/HTTP exporter 1.45).
- GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-14-rezerve-assistant-design.md`.
- **Sections:**
  - §5: inbound authentication.
  - §6.5: the kapora event, the agent's side.
  - §7.2: the turn handler, the owner's state.
  - §7.5: budgets.
  - §7.6: the owner relay.
  - §7.7: reminders.
  - §7.9: Telegram.
  - §7.10: observability.
  - §7.11: configuration.
  - §10: CI.
  - §11's phase 4.
- **Plan 4b, not this one:** §9's deployment, Rezerve's side of §6.5, and the live checks.
- **Agreed ordering (2026-10-16):** budget caps land before any public Cloud Run URL exists. This plan creates no cloud resource, and Tasks 2–3 are the budgets.

## Global Constraints

- **Free tiers only, $0 a month.** Nothing here creates a cloud resource or calls a paid API. Tests use fakes for Cloud Tasks, Google's token check, Telegram's Bot API and Langfuse.
- **Tests and migrations run only on the Compose Postgres.** Use `TEST_DATABASE_URL`, `postgresql+asyncpg://postgres:postgres@localhost:5433/assistant_test`, never a Neon URL.
- **Never read or print a secret.** The Telegram bot token is part of every Bot API URL. So no URL of the Telegram client is logged, traced, or put in an error message (Task 6), and httpx is not traced (Task 12).
- **No real personal data on the free tiers.** Traces leave out what customers wrote unless `TRACE_CONTENT=true` (Task 12). Scenario and test customers are invented.
- **Turkish where a customer or owner reads it;** identifiers are English. ruff ignores RUF001–003, as it already does.
- **The dependency pin stays.** `[tool.uv] exclude-newer = "2026-10-15T00:00:00Z"`. Every `uv add` below resolves under it, to the versions in the Tech Stack.
- **Every task ends with all four checks clean,** each output read:
  - `uv run pytest -q`
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run pyright`

  pyright is strict on `src/`. Never judge a check by a piped `tail`: a pipe hides the exit code.
- **Don't run `pytest` during an eval run.** One of its tests resets the eval business.
- **Commit trailer:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Branch:** `feat/production-service`, from `main` at 0281660. This plan is its first commit.

## Review Focus

These are the inputs the spec implies but no test exercised until now, most likely to bite first:

1. **The demo's owner trying the bot from their own chat.** The chat linked as the owner's is also a customer's chat, so handoff notices, relays and the customer's own messages all meet in one chat. What the owner replies to decides who is speaking. Test: Task 9, `test_an_owner_testing_as_their_own_customer_is_told_apart_by_reply`.
2. **Cloud Run's `X-Forwarded-For` layout.** The per-address widget limit (Task 3) counts the entry `TRUSTED_PROXY_HOPS` places from the right. Which entry is the real client on Cloud Run can only be seen there. Plan 4b logs one real request before trusting the default of 1. Test: Task 3, `test_the_client_is_counted_from_the_right_of_x_forwarded_for`, pins the rule.
3. **Telegram refusing a reply for a moment, mid-batch.** The channel raises on a 429 or 5xx (Task 6), the turn has committed, and the queue retries. The retry sends only what was never marked sent (at-least-once, spec §7.2). Tests: Task 6's `test_a_passing_failure_is_raised_so_the_turn_is_retried`, and the existing `test_a_crash_between_send_and_mark_resends_only_the_unsent`.
4. **Langfuse's ingestion format.** It needs the full `…/api/public/otel/v1/traces` path, and `x-langfuse-ingestion-version=4` in the headers, without which ingestion can lag up to ten minutes. Only a live trace can show both work; plan 4b's smoke checks look. Test: Task 12's `test_traces_go_to_the_endpoint_and_nowhere_else` pins what is sent.
5. **Concurrent turns overshooting the daily cap.** The cap sums finished turns' requests, so turns running at once can each pass the check. Accepted: the cap guards the free quota, it is not an exact meter. Free-tier quotas sit above the 400 default, and Cloud Tasks runs at most three turns at once (plan 4b's queue settings).

## Verified before this plan was written

Every code block below ran in a prototype, a clone of `main` at 0281660, between 3 and 5 November 2026:

- **The full suite:** 427 tests to 505, with pyright strict and ruff clean. Each task was applied in order and committed separately, and each task's count below is that commit's.
- **A fresh database:**
  - migrations 0004–0008 ran up from zero, down to base and up again;
  - `alembic check` found no difference between the models and the migrations.
- **The Docker image** builds, and imports every new module.
- **Logfire's real configuration** ran against a closed port:
  - spans recorded with valid trace ids;
  - the session id reached the spans as baggage;
  - export failures were only logged.
- **The GitHub Actions** used are their current majors, with their inputs checked: `actions/checkout@v7`, `astral-sh/setup-uv@v10`, `docker/setup-buildx-action@v4`, `docker/build-push-action@v7`.
- **Checked against live documentation:**
  - Telegram's `X-Telegram-Bot-Api-Secret-Token`;
  - Langfuse's EU traces endpoint, its `langfuse.session.id` and `langfuse.user.id` attributes, and its ingestion-version header;
  - Cloud Run's region tiers;
  - Google's OIDC verification errors: `ValueError` for a bad token, `GoogleAuthError` for a wrong issuer, `TransportError` when its keys are out of reach.

**How to read the code blocks.** A whole file is written as shown. A `diff` block is the change to make, by hand or with `git apply`. Its context lines are exactly what the file holds at that point when the tasks run in order: the plan was replayed that way, task by task, on a fresh clone of `main`.

## Rulings made while prototyping

Each comes with what it costs if wrong.

- **The daily request cap sums `agent_runs`** instead of keeping the spec's `daily_usage` counter row. Every turn's requests are already metered there, so one source of truth is enough. An index on `started_at` keeps the sum cheap. If wrong, it costs a small table and a counter update per turn.
- **Trace settings are `OTEL_TRACES_ENDPOINT` and `OTEL_HEADERS`,** not the spec's `OTEL_EXPORTER_OTLP_*`. Settings come from the mounted JSON secret, which OpenTelemetry's own environment reading never sees. So the service builds its exporter from its settings. If wrong, it costs renaming two keys.
- **httpx is not traced,** although spec §7.10 lists it. Telegram's Bot API URLs carry the bot token, and a traced URL would put it in Langfuse. The model calls are traced through Pydantic AI, and the requests through FastAPI. If wrong, the cost is fewer spans per trace.
- **Traces carry no customer text unless `TRACE_CONTENT=true`.** It is personal data, and Langfuse keeps it 30 days. If wrong, it costs flipping a setting.
- **Tables beyond spec §7.8:**
  - `telegram_chats`, which business each chat talks to;
  - `processed_events`, Rezerve's events already acted on;
  - in `reminders`, the business, customer ref and chat id a reminder needs when it fires;
  - in `conversations`, `handed_off_at` for the owner's 24 hours.

  If wrong, it costs a migration.
- **The owner relay:**
  - Direction `owner` marks a message to the business owner's chat.
  - The owner's replies become ordinary outbound messages ("İşletme: …"), so the widget and Telegram show them like any reply.
  - `/id` is added, so an owner can learn the chat id that `assistant owners link` needs.

  If wrong, it costs a few lines in the webhook.
- **The event contract, which plan 4b's Rezerve side implements:**
  - `POST /events/rezerve`;
  - `X-Rezerve-Timestamp` in Unix seconds;
  - `X-Rezerve-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw body>" under ASSISTANT_EVENT_SECRET>`;
  - a five-minute window either way;
  - the body `{"type": "booking.confirmed", "booking_id", "business_slug", "customer_ref", "service_name", "local_label"}`.

  If wrong, both sides change together.
- **The per-address widget limit is an in-process sliding window.** Each of at most two instances counts on its own. If wrong, an address gets at most twice its limit, and the daily cap still holds.
- **`telegram_chats` rows are never pruned.** Each holds a chat id and a business slug, no transcript. The KVKK pruning, after 90 days idle, covers transcripts, and `reminders` and `processed_events` prune themselves the same way. If wrong, it costs one more `DELETE` in `bind_telegram_chat`.
- **`/start <slug>` is not checked with Rezerve.** A chat bound to a slug that has no business, or no assistant, finds out on its first message: Rezerve refuses the turn, and the customer gets the booking-page reply. If wrong, it costs one MCP call on `/start`.
- **The evals switch every budget off** in `EvalSettings`. A whole run and its trials spend more in a day than production's cap. If wrong, eval runs get the booking page instead of answers.
- **Telegram text beyond 1000 characters is cut to 1000,** the length the widget accepts. If wrong, the cost is a long message's tail.
- **Only a booking still `confirmed` at its time is reminded.** A pending kapora hold expires in 30 minutes anyway. If wrong, it costs one status check.
- **The region is europe-west1, for plan 4b.** Cloud Run's free tier is a Tier-1-priced discount per billing account, and europe-west3 is Tier 2. The spec's own fallback is europe-west1. If wrong, Neon in Frankfurt is about 10 ms further.

## File structure

**Created:**

| File | Responsibility |
|---|---|
| `src/assistant/agent/budget.py` | The caps a turn checks before any model call (spec §7.5) |
| `src/assistant/ratelimit.py` | A sliding window per client address, and which `X-Forwarded-For` entry is the client |
| `src/assistant/queue/cloud_tasks.py` | Turns and reminders as named Cloud Tasks, calling the service back with OIDC |
| `src/assistant/internal.py` | `/internal/turn` and `/internal/reminder`, behind Google's token check |
| `src/assistant/channels/telegram.py` | The Bot API client and the Telegram channel |
| `src/assistant/channels/telegram_webhook.py` | Telegram's webhook: secret check, `/start <slug>`, `/id`, the owner's replies |
| `src/assistant/cli.py` | `assistant owners link|unlink` |
| `src/assistant/reminders.py` | What a turn's writes booked, and sending a reminder when its task fires |
| `src/assistant/events.py` | Rezerve's signed events |
| `src/assistant/observability.py` | Traces over OTLP/HTTP, and values every span inside carries |
| `src/assistant/db/alembic/versions/0004_budget_indexes.py` … `0008_processed_events.py` | One migration per task that needs one |
| `.github/workflows/ci.yml` | Lint, types, tests on Postgres, the image |
| `tests/fakes/tasks.py`, `telegram.py`, `channels.py`, `queue.py` | Cloud Tasks, the Bot API, a Telegram outbox, a recording queue |

**Modified:**
- **Code:** `src/assistant/config.py`, `services.py`, `main.py`, `mcp.py`, `db/models.py`, `conversation/store.py`, `conversation/turn.py`, `agent/handoff.py`, `channels/web.py`, `queue/base.py` and `queue/local.py`; `evals/config.py`.
- **Tests:** `tests/conftest.py`, and the tests beside each change.
- **Project and docs:** `pyproject.toml` and `uv.lock`, `README.md`, `.env.example`.

---

### Task 1: Settings from one mounted secret

In production every setting comes from one Secret Manager secret: a JSON object keyed like the environment, mounted as a file that `CONFIG_FILE` names (spec §7.11). The environment still overrides it. This task also declares every setting phase 4 needs, so later tasks only read them.

**Files:**
- Modify: `src/assistant/config.py` (whole file below)
- Test: `tests/unit/test_config.py` (whole file below)

**Interfaces:**
- Produces, on `Settings`:
  - `public_base_url: str`;
  - `assistant_event_secret`, `telegram_bot_token` and `telegram_webhook_secret`, each `SecretStr | None`;
  - `queue_backend: Literal["local", "cloud_tasks"]`;
  - `gcp_project: str | None`, `gcp_region: str`, `tasks_queue_turns: str`, `tasks_queue_reminders: str`, `tasks_invoker_sa: str | None`;
  - `daily_request_cap: int | None`, `conversation_daily_token_cap: int | None`;
  - `sender_message_limit: int`, which Task 3 makes `int | None`;
  - `sender_window_seconds: int`, `ip_message_limit: int`;
  - `otel_traces_endpoint: str | None`, `otel_headers: SecretStr | None`, `trace_content: bool`.
- Produces: `DatabaseSettings.settings_customise_sources`, which adds a `JsonConfigSettingsSource` for the file `CONFIG_FILE` names. Alembic reads the same file.
- Produces: `QUEUE_BACKEND=cloud_tasks` without `GCP_PROJECT` and `TASKS_INVOKER_SA` fails validation.

- [ ] **Step 1: Write the failing tests**

Replace `tests/unit/test_config.py` with:

```python
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant.config import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ASSISTANT_MCP_TOKEN",
        "CUSTOMER_REF_SECRET",
        "ALLOWED_WEB_ORIGINS",
        "MODEL_FALLBACKS",
        "CONFIG_FILE",
        "DATABASE_URL",
        "DAILY_REQUEST_CAP",
        "QUEUE_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)


def test_lists_come_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_WEB_ORIGINS", "http://localhost:3000, https://rezerve.app")
    monkeypatch.setenv("MODEL_FALLBACKS", "groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b")
    settings = Settings(_env_file=None, assistant_mcp_token="t", customer_ref_secret="s")  # pyright: ignore[reportCallIssue]
    assert settings.allowed_web_origins == ["http://localhost:3000", "https://rezerve.app"]
    assert settings.model_fallbacks == ["groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b"]


def test_the_secrets_have_no_defaults() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def secret_file(tmp_path: Path, values: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_production_reads_every_setting_from_one_mounted_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Spec §7.11: one Secret Manager secret, mounted as a file, keyed the way
    # the environment would be.
    path = secret_file(
        tmp_path,
        {
            "DATABASE_URL": "postgresql+asyncpg://neon.example/assistant",
            "ASSISTANT_MCP_TOKEN": "token",
            "CUSTOMER_REF_SECRET": "ref-secret",
            "TELEGRAM_BOT_TOKEN": "123:abc",
            "DAILY_REQUEST_CAP": 250,
        },
    )
    monkeypatch.setenv("CONFIG_FILE", str(path))
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.database_url == "postgresql+asyncpg://neon.example/assistant"
    assert settings.customer_ref_secret.get_secret_value() == "ref-secret"
    assert settings.telegram_bot_token is not None
    assert settings.telegram_bot_token.get_secret_value() == "123:abc"
    assert settings.daily_request_cap == 250


def test_the_environment_still_overrides_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = secret_file(
        tmp_path,
        {"ASSISTANT_MCP_TOKEN": "t", "CUSTOMER_REF_SECRET": "s", "DAILY_REQUEST_CAP": 250},
    )
    monkeypatch.setenv("CONFIG_FILE", str(path))
    monkeypatch.setenv("DAILY_REQUEST_CAP", "50")

    assert Settings(_env_file=None).daily_request_cap == 50  # pyright: ignore[reportCallIssue]


def test_cloud_tasks_needs_a_project_and_an_invoker() -> None:
    with pytest.raises(ValidationError, match="GCP_PROJECT and TASKS_INVOKER_SA"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            assistant_mcp_token="t",
            customer_ref_secret="s",
            queue_backend="cloud_tasks",
        )
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: `3 failed, 2 passed`. The three new tests fail:
- the two secret-file tests, on `AttributeError` (`telegram_bot_token`) or a `ValidationError` for the missing secrets;
- the Cloud Tasks test, because nothing is raised.

- [ ] **Step 3: Read the secret and declare phase 4's settings**

Replace `src/assistant/config.py` with:

```python
"""Settings, read from the environment (and from `.env` in development).

Secrets have no defaults, so a missing one stops the service at startup
rather than at its first customer. In production every setting comes from one
Secret Manager secret (JSON), mounted as a file named by CONFIG_FILE (spec
§7.11); the environment still wins over it, for a one-off override.
"""

import os
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class DatabaseSettings(BaseSettings):
    """Just the database, for Alembic: migrating needs no API keys."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/assistant"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources = [init_settings, env_settings, dotenv_settings]
        if config_file := os.environ.get("CONFIG_FILE"):
            sources.append(JsonConfigSettingsSource(settings_cls, json_file=config_file))
        return (*sources, file_secret_settings)


class Settings(DatabaseSettings):
    # The widget's origins, comma-separated: "http://localhost:3000,https://rezerve.app".
    allowed_web_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    # This service's own address: Cloud Tasks calls it back there, with OIDC
    # tokens minted for it.
    public_base_url: str = "http://localhost:8000"

    rezerve_mcp_url: str = "http://localhost:3000/api/mcp"
    assistant_mcp_token: SecretStr
    customer_ref_secret: SecretStr
    # Rezerve signs its events with this (spec §6.5); without it they are refused.
    assistant_event_secret: SecretStr | None = None

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    # "<provider>:<model>"; the eval report decides the defaults.
    model_primary: str = "google:gemini-3.5-flash-lite"
    model_fallbacks: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["groq:openai/gpt-oss-120b"]
    )

    # Telegram (spec §7.9): off without a bot token.
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None

    # Where turns and reminders wait: in this process, or in Cloud Tasks.
    queue_backend: Literal["local", "cloud_tasks"] = "local"
    gcp_project: str | None = None
    gcp_region: str = "europe-west1"
    tasks_queue_turns: str = "turns"
    tasks_queue_reminders: str = "reminders"
    # The service account Cloud Tasks signs its calls as.
    tasks_invoker_sa: str | None = None

    run_request_limit: int = 8
    run_token_limit: int = 40_000
    approval_ttl_seconds: int = 600
    lease_ttl_seconds: int = 120
    lease_renew_seconds: int = 30

    # Budgets (spec §7.5). None switches a cap off, as the evals do.
    daily_request_cap: int | None = 400
    conversation_daily_token_cap: int | None = 40_000
    sender_message_limit: int = 20
    sender_window_seconds: int = 600
    # The widget's posts per client IP, in the same window.
    ip_message_limit: int = 60

    # Traces over OTLP/HTTP (spec §7.10): Langfuse's traces endpoint, and its
    # headers as "Authorization=Basic <base64 of public:secret key>".
    otel_traces_endpoint: str | None = None
    otel_headers: SecretStr | None = None
    # Whether traces carry what customers wrote; off, since it is personal data.
    trace_content: bool = False

    @field_validator("allowed_web_origins", "model_fallbacks", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _cloud_tasks_is_complete(self) -> Self:
        if self.queue_backend == "cloud_tasks" and not (self.gcp_project and self.tasks_invoker_sa):
            raise ValueError("QUEUE_BACKEND=cloud_tasks needs GCP_PROJECT and TASKS_INVOKER_SA")
        return self
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `430 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/config.py tests/unit/test_config.py
git commit -m "feat(config): every setting from one mounted secret, and phase 4's settings

In production the settings come from one Secret Manager secret, mounted as a
JSON file that CONFIG_FILE names (spec §7.11); the environment still wins.
Settings for Telegram, Cloud Tasks, the budgets and traces are declared here.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The daily caps

Spec §7.5: 400 model requests a day across the service, and 40,000 tokens a day per conversation. A turn checks both before any model call. Past either, the customer still gets an answer, the booking page, and the turn is recorded as `degraded`. The counts are sums over `agent_runs` in the last 24 hours, and two indexes keep those sums cheap. The evals switch both caps off.

**Files:**
- Create: `src/assistant/agent/budget.py`, `src/assistant/db/alembic/versions/0004_budget_indexes.py`
- Modify: `src/assistant/conversation/store.py`, `src/assistant/conversation/turn.py`, `src/assistant/db/models.py`, `evals/config.py`
- Test: `tests/integration/test_turn.py`, `tests/evals/test_run.py`

**Interfaces:**
- Consumes: `Settings.daily_request_cap`, `Settings.conversation_daily_token_cap` (Task 1).
- Produces: `Budget(daily_requests: int | None = None, conversation_daily_tokens: int | None = None)`, frozen, with `async spent(store, conversation_id: int, now: datetime) -> Spent | None`. Task 3 changes `spent`'s second parameter to the `ConversationRecord`.
- Produces: `TurnSettings.budget: Budget`, defaulting to `Budget()`, which has no limits. `TurnSettings.from_settings` fills it.
- Produces: `Store.requests_since(since: datetime) -> int` and `Store.tokens_since(conversation_id: int, since: datetime) -> int`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_turn.py`, import `Budget` and `RunRecord`, and append three tests and their helper:

```diff
--- a/tests/integration/test_turn.py
+++ b/tests/integration/test_turn.py
@@ -20,9 +20,10 @@ from pydantic_ai.models.function import AgentInfo, FunctionModel
 
 from assistant.agent.agent import build_agent
 from assistant.agent.approvals import EXPIRED, ONE_AT_A_TIME, ReplyClassifier
+from assistant.agent.budget import Budget
 from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
 from assistant.conversation.lease import Lease, LeaseLost
-from assistant.conversation.store import Reply, Store, TurnResult
+from assistant.conversation.store import Reply, RunRecord, Store, TurnResult
 from assistant.conversation.turn import TurnProcessor, TurnSettings
 from assistant.identity import customer_ref
 from assistant.mcp import RezerveSession
@@ -513,3 +514,75 @@ async def test_a_turn_counts_the_calls_that_failed(store: Store) -> None:
     await turn.say("yarın 15:30 cilt bakımı, adım Elif Kaya")
     [run] = await store.runs(turn.conversation_id)
     assert (run.outcome, run.tool_errors) == ("reply", 2)
+
+
+UNAVAILABLE = (
+    "Şu an yanıt veremiyorum — randevunuzu buradan alabilirsiniz: http://localhost:3000/r/demo"
+)
+
+
+def spent(started_at: datetime, *, requests: int = 0, tokens: int = 0) -> TurnResult:
+    """A turn that has already spent what a test needs spent."""
+    run = RunRecord(
+        started_at=started_at,
+        duration_ms=1,
+        model_used="gemini",
+        models_tried=["gemini"],
+        requests=requests,
+        input_tokens=tokens,
+        output_tokens=0,
+        tool_calls=0,
+        tool_errors=0,
+        outcome="reply",
+    )
+    return TurnResult(processed=[], replies=[], run=run)
+
+
+async def test_a_spent_daily_request_cap_answers_without_a_model(store: Store) -> None:
+    # Spec §7.5: the free tier's daily requests are the whole service's. Once
+    # they are spent, every customer still gets an answer: the booking page.
+    elsewhere = await store.record_inbound(
+        InboundMessage("demo", "web", "visitor-2", str(uuid4()), "merhaba"),
+        customer_ref("secret", "web", "visitor-2"),
+        datetime.now(UTC),
+    )
+    turn = Harness(store, Script())  # an empty script: a model call would fail the test
+    await store.commit_turn(
+        elsewhere.conversation_id,
+        spent(turn.clock.now - timedelta(hours=1), requests=400),
+        turn.clock.now,
+    )
+    turn.settings = dataclasses.replace(turn.settings, budget=Budget(daily_requests=400))
+
+    assert await turn.say("merhaba") == [UNAVAILABLE]
+    [run] = await store.runs(turn.conversation_id)
+    assert (run.outcome, run.requests) == ("degraded", 0)
+    assert await store.unprocessed_inbound(turn.conversation_id) == []
+
+
+async def test_requests_older_than_a_day_no_longer_count(store: Store) -> None:
+    turn = Harness(store, Script(says("Merhaba!")))
+    await turn.receive("merhaba")
+    await store.commit_turn(
+        turn.conversation_id,
+        spent(turn.clock.now - timedelta(hours=25), requests=400),
+        turn.clock.now,
+    )
+    turn.settings = dataclasses.replace(turn.settings, budget=Budget(daily_requests=400))
+
+    assert await turn.say("yer var mı?") == ["Merhaba!"]
+
+
+async def test_a_conversation_past_its_daily_tokens_gets_the_booking_page(store: Store) -> None:
+    turn = Harness(store, Script())
+    await turn.receive("merhaba")
+    await store.commit_turn(
+        turn.conversation_id,
+        spent(turn.clock.now - timedelta(hours=2), tokens=40_000),
+        turn.clock.now,
+    )
+    turn.settings = dataclasses.replace(
+        turn.settings, budget=Budget(conversation_daily_tokens=40_000)
+    )
+
+    assert await turn.say("yarın yer var mı?") == [UNAVAILABLE]
```

In `tests/evals/test_run.py`:

```diff
--- a/tests/evals/test_run.py
+++ b/tests/evals/test_run.py
@@ -2,6 +2,9 @@ from datetime import UTC, datetime
 
 import pytest
 
+from assistant.agent.budget import Budget
+from assistant.conversation.turn import TurnSettings
+from evals.config import EvalSettings
 from evals.results import RunConfig, RunResult, ScenarioResult
 from evals.run import (
     agent_models,
@@ -118,3 +121,9 @@ def test_a_run_is_one_trial_unless_told_and_a_resume_keeps_its_own() -> None:
 def test_a_resume_refuses_flags_the_run_did_not_start_with(flags: list[str], refused: str) -> None:
     with pytest.raises(SystemExit, match=refused):
         check_resume(parse_args([*flags, "--resume", GROQ_RUN.id]), GROQ_RUN)
+
+
+def test_the_evals_measure_the_agent_not_its_budget() -> None:
+    # A whole run and its trials spend more in a day than production's cap.
+    settings = EvalSettings(_env_file=None, assistant_mcp_token="t", customer_ref_secret="s")  # pyright: ignore[reportCallIssue]
+    assert TurnSettings.from_settings(settings).budget == Budget()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/integration/test_turn.py tests/evals/test_run.py -q`
Expected: both files fail to collect: `ModuleNotFoundError: No module named 'assistant.agent.budget'`.

- [ ] **Step 3: Write the budget, the sums, the indexes and the check**

Create `src/assistant/agent/budget.py`:

```python
"""What the free tiers can bear (spec §7.5), checked before any model call.

The counts come from what the service already keeps: `agent_runs` holds every
turn's model requests and tokens. None switches a limit off.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from assistant.conversation.store import Store

type Spent = Literal["daily_requests", "conversation_tokens"]

DAY = timedelta(days=1)


@dataclass(frozen=True)
class Budget:
    # Model requests in the last 24 hours, across every conversation: the
    # free tier's daily quota belongs to the whole service.
    daily_requests: int | None = None
    # Tokens one conversation may use in the last 24 hours.
    conversation_daily_tokens: int | None = None

    async def spent(self, store: Store, conversation_id: int, now: datetime) -> Spent | None:
        """Which limit this turn would cross, or None."""
        if (
            self.daily_requests is not None
            and await store.requests_since(now - DAY) >= self.daily_requests
        ):
            return "daily_requests"
        if self.conversation_daily_tokens is not None:
            used = await store.tokens_since(conversation_id, now - DAY)
            if used >= self.conversation_daily_tokens:
                return "conversation_tokens"
        return None
```

Create `src/assistant/db/alembic/versions/0004_budget_indexes.py`:

```python
"""Indexes for the budgets: the last day's runs, the service's and a conversation's.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("agent_runs_started_at", "agent_runs", ["started_at"])
    op.create_index(
        "agent_runs_conversation_started_at", "agent_runs", ["conversation_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("agent_runs_conversation_started_at", table_name="agent_runs")
    op.drop_index("agent_runs_started_at", table_name="agent_runs")
```

In `src/assistant/db/models.py`, the same two indexes on `AgentRun`:

```diff
--- a/src/assistant/db/models.py
+++ b/src/assistant/db/models.py
@@ -152,4 +152,7 @@ class AgentRun(Base):
             "outcome IN ('reply', 'confirmation', 'handoff', 'degraded', 'error')",
             name="agent_runs_outcome",
         ),
+        # The budgets sum the last day's runs: the service's, and one conversation's.
+        sa.Index("agent_runs_started_at", "started_at"),
+        sa.Index("agent_runs_conversation_started_at", "conversation_id", "started_at"),
     )
```

In `src/assistant/conversation/store.py`, the two sums, before `decisions`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -391,6 +391,26 @@ class Store:
                 for row in rows.scalars()
             ]
 
+    async def requests_since(self, since: datetime) -> int:
+        """Model requests made since `since`, by every conversation."""
+        async with self._sessions() as session:
+            total = await session.execute(
+                sa.select(sa.func.coalesce(sa.func.sum(AgentRun.requests), 0)).where(
+                    AgentRun.started_at >= since
+                )
+            )
+            return int(total.scalar_one())
+
+    async def tokens_since(self, conversation_id: int, since: datetime) -> int:
+        """Tokens one conversation's turns have used since `since`."""
+        async with self._sessions() as session:
+            total = await session.execute(
+                sa.select(
+                    sa.func.coalesce(sa.func.sum(AgentRun.input_tokens + AgentRun.output_tokens), 0)
+                ).where(AgentRun.conversation_id == conversation_id, AgentRun.started_at >= since)
+            )
+            return int(total.scalar_one())
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

In `src/assistant/conversation/turn.py`, the budget on `TurnSettings`, and the check at the top of `_answer`:

```diff
--- a/src/assistant/conversation/turn.py
+++ b/src/assistant/conversation/turn.py
@@ -45,6 +45,7 @@ from assistant.agent.approvals import (
     write_actions,
     write_receipt,
 )
+from assistant.agent.budget import Budget
 from assistant.agent.handoff import booking_page, handoff_reply, unavailable_reply
 from assistant.channels.base import Channel, ChannelName
 from assistant.config import Settings
@@ -85,6 +86,8 @@ class TurnSettings:
     lease_ttl: timedelta
     lease_renew: timedelta
     rezerve_mcp_url: str
+    # Spec §7.5; the default has no limits, as tests and the evals want.
+    budget: Budget = field(default_factory=Budget)
 
     @classmethod
     def from_settings(cls, settings: Settings) -> "TurnSettings":
@@ -95,6 +98,10 @@ class TurnSettings:
             lease_ttl=timedelta(seconds=settings.lease_ttl_seconds),
             lease_renew=timedelta(seconds=settings.lease_renew_seconds),
             rezerve_mcp_url=settings.rezerve_mcp_url,
+            budget=Budget(
+                daily_requests=settings.daily_request_cap,
+                conversation_daily_tokens=settings.conversation_daily_token_cap,
+            ),
         )
 
 
@@ -263,6 +270,11 @@ class TurnProcessor:
         check_lease: Callable[[], None],
     ) -> TurnResult:
         turn = _Turn(processed=[message.seq for message in batch], started_at=self._clock())
+        spent = await self._settings.budget.spent(self._store, conversation.id, self._clock())
+        if spent is not None:
+            # No model call, and still an answer (spec §7.5).
+            log.warning("Conversation %s: the %s budget is spent", conversation.id, spent)
+            return turn.finish([Reply(self._unavailable(conversation, turn))], "degraded")
         try:
             rezerve = self._open_rezerve(conversation.business_slug, conversation.customer_ref)
             # The session records each write as it runs: kept on the turn, so
```

In `evals/config.py`, both caps off for the evals:

```diff
--- a/evals/config.py
+++ b/evals/config.py
@@ -12,3 +12,7 @@ class EvalSettings(Settings):
     # The simulated customer and the judge.
     simulator_model: str = "groq:openai/gpt-oss-120b"
     judge_model: str = "groq:openai/gpt-oss-120b"
+    # The evals measure the agent, not its budgets: a whole run and its trials
+    # spend more in a day than production's caps allow.
+    daily_request_cap: int | None = None
+    conversation_daily_token_cap: int | None = None
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/integration/test_turn.py tests/evals/test_run.py -q`
Expected: every test passes.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `434 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/agent/budget.py src/assistant/db/alembic/versions/0004_budget_indexes.py \
  src/assistant/db/models.py src/assistant/conversation/store.py src/assistant/conversation/turn.py \
  evals/config.py tests/integration/test_turn.py tests/evals/test_run.py
git commit -m "feat(budget): the daily caps, checked before any model call

Spec §7.5: 400 model requests a day across the service and 40,000 tokens a day
per conversation, summed from agent_runs. Past either, the customer gets the
booking page and the turn is recorded as degraded. The evals switch both off.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The sender limit, and the widget's limit per address

Spec §7.5: 20 messages per sender in 10 minutes. Past it, the sender is told once, "biraz yavaşlayalım", and then gets no answer until they slow down. Spec §5 also limits the widget per IP, because a visitor id costs nothing to make. The widget's posts are counted per client address in an in-process sliding window, and a full window answers 429. The client address is counted from the right of `X-Forwarded-For`, where the platform appends it, and never from the left, where a client can write anything.

**Files:**
- Create: `src/assistant/ratelimit.py`
- Modify: `src/assistant/agent/budget.py` (whole file below), `src/assistant/conversation/store.py`, `src/assistant/conversation/turn.py`, `src/assistant/config.py`, `src/assistant/channels/web.py`, `src/assistant/services.py`, `src/assistant/main.py`, `evals/config.py`
- Test: `tests/unit/test_ratelimit.py`, `tests/integration/test_turn.py`, `tests/integration/test_web.py`

**Interfaces:**
- Consumes: `Budget` and `TurnSettings.budget` (Task 2).
- Produces: `Budget.sender_messages: int | None` and `Budget.sender_window: timedelta`, ten minutes by default. `Budget.spent(store, conversation: ConversationRecord, now)` now takes the conversation, and can return `"sender_rate"`.
- Produces: `SLOW_DOWN: str` in `assistant.agent.budget`.
- Produces: `Store.inbound_since(channel, channel_user_id, since) -> int` and `Store.said_since(conversation_id, text, since) -> bool`.
- Produces: `SlidingWindow(*, limit: int, seconds: float, clock=time.monotonic, max_keys=10_000)` with `allow(key) -> bool` and `len()`, and `client_ip(request, *, hops: int) -> str`.
- Produces: `AppServices.post_limit: SlidingWindow`.
- Produces: `Settings.trusted_proxy_hops: int = 1`, and `Settings.sender_message_limit: int | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ratelimit.py`:

```python
from starlette.requests import Request

from assistant.ratelimit import SlidingWindow, client_ip


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_key_gets_its_limit_in_a_window_and_no_more() -> None:
    clock = Clock()
    window = SlidingWindow(limit=2, seconds=600, clock=clock)
    assert [window.allow("a"), window.allow("a"), window.allow("a")] == [True, True, False]
    assert window.allow("b")


def test_the_window_slides() -> None:
    clock = Clock()
    window = SlidingWindow(limit=1, seconds=600, clock=clock)
    assert window.allow("a")
    clock.now += 599
    assert not window.allow("a")
    clock.now += 2
    assert window.allow("a")


def test_keys_whose_window_has_passed_are_forgotten() -> None:
    clock = Clock()
    window = SlidingWindow(limit=1, seconds=600, clock=clock, max_keys=2)
    for key in "abc":
        window.allow(key)
        clock.now += 601
    assert len(window) <= 2


def request(forwarded_for: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    return Request({"type": "http", "headers": headers, "client": ("10.0.0.9", 51000)})


def test_the_client_is_counted_from_the_right_of_x_forwarded_for() -> None:
    # Entries on the left come from the client, who can write anything there;
    # the ones the platform appended are on the right.
    assert client_ip(request("6.6.6.6, 203.0.113.7"), hops=1) == "203.0.113.7"
    assert client_ip(request("6.6.6.6, 203.0.113.7, 35.191.0.1"), hops=2) == "203.0.113.7"


def test_without_the_header_the_connection_is_the_client() -> None:
    assert client_ip(request(None), hops=1) == "10.0.0.9"
    assert client_ip(request("203.0.113.7"), hops=2) == "10.0.0.9"
```

In `tests/integration/test_turn.py`, import `SLOW_DOWN`, and append:

```diff
--- a/tests/integration/test_turn.py
+++ b/tests/integration/test_turn.py
@@ -20,7 +20,7 @@ from pydantic_ai.models.function import AgentInfo, FunctionModel
 
 from assistant.agent.agent import build_agent
 from assistant.agent.approvals import EXPIRED, ONE_AT_A_TIME, ReplyClassifier
-from assistant.agent.budget import Budget
+from assistant.agent.budget import SLOW_DOWN, Budget
 from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
 from assistant.conversation.lease import Lease, LeaseLost
 from assistant.conversation.store import Reply, RunRecord, Store, TurnResult
@@ -586,3 +586,17 @@ async def test_a_conversation_past_its_daily_tokens_gets_the_booking_page(store:
     )
 
     assert await turn.say("yarın yer var mı?") == [UNAVAILABLE]
+
+
+async def test_a_sender_past_the_limit_is_asked_to_slow_down_once_then_not_answered(
+    store: Store,
+) -> None:
+    # Spec §7.5: 20 messages in 10 minutes, here 3. Over the limit the sender
+    # hears it once, then nothing, and no model is asked either way.
+    turn = Harness(store, Script(says("Merhaba!")))
+    turn.settings = dataclasses.replace(turn.settings, budget=Budget(sender_messages=3))
+
+    assert await turn.say("selam", "orada mısınız", "hey") == ["Merhaba!"]
+    assert await turn.say("??") == [SLOW_DOWN]
+    assert await turn.say("cevap verin") == []
+    assert await store.unprocessed_inbound(turn.conversation_id) == []
```

In `tests/integration/test_web.py`, the fixture takes settings a test can override, and one test does:

```diff
--- a/tests/integration/test_web.py
+++ b/tests/integration/test_web.py
@@ -58,7 +58,15 @@ def script() -> Script:
 
 
 @pytest.fixture
-async def widget(database_url: str, store: Store, script: Script) -> AsyncIterator[Widget]:
+def widget_overrides() -> dict[str, Any]:
+    """Settings a test changes, by parametrizing this fixture's name."""
+    return {}
+
+
+@pytest.fixture
+async def widget(
+    database_url: str, store: Store, script: Script, widget_overrides: dict[str, Any]
+) -> AsyncIterator[Widget]:
     # `store` empties the tables; the app opens its own engine on them.
     fake = FakeRezerve()
     settings = Settings(
@@ -67,6 +75,7 @@ async def widget(database_url: str, store: Store, script: Script) -> AsyncIterat
         assistant_mcp_token="t",
         customer_ref_secret="s",
         allowed_web_origins=[ORIGIN],
+        **widget_overrides,
     )
     app = create_app(settings, model=script.model, open_rezerve=lambda slug, ref: fake.session())
     async with app.router.lifespan_context(app):
@@ -171,3 +180,16 @@ async def test_a_browser_may_send_the_visitor_header(widget: Widget) -> None:
     )
     assert allowed.status_code == 200
     assert "x-visitor-id" in allowed.headers["access-control-allow-headers"].lower()
+
+
+@pytest.mark.parametrize("widget_overrides", [{"ip_message_limit": 2}])
+async def test_one_address_posting_too_fast_is_turned_away(widget: Widget, script: Script) -> None:
+    # A visitor id costs nothing to make, so the widget also counts per client
+    # address (spec §5): new visitors from one address share its limit.
+    script.add(says("Merhaba!"), says("Merhaba!"))
+    for _ in range(2):
+        widget.visitor_id = str(uuid4())
+        assert (await widget.post("merhaba")).status_code == 202
+    widget.visitor_id = str(uuid4())
+    assert (await widget.post("merhaba")).status_code == 429
+    await widget.settle()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/test_ratelimit.py tests/integration/test_turn.py tests/integration/test_web.py -q`
Expected: collection fails:
- `ImportError: cannot import name 'SLOW_DOWN' from 'assistant.agent.budget'`;
- `ModuleNotFoundError: No module named 'assistant.ratelimit'`.

- [ ] **Step 3: Write the window, the sender count and their checks**

Create `src/assistant/ratelimit.py`:

```python
"""A sliding window per key, kept in this process: the widget's posts per
client address (spec §5).

Each Cloud Run instance keeps its own window, and the service runs at most
two, so one address gets at most twice the limit. The daily model-request cap
stays the hard stop; this keeps one address from spending it alone.
"""

import time
from collections import deque
from collections.abc import Callable

from starlette.requests import Request


class SlidingWindow:
    def __init__(
        self,
        *,
        limit: int,
        seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._seconds = seconds
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def __len__(self) -> int:
        return len(self._hits)

    def allow(self, key: str) -> bool:
        """Count one hit for `key`, unless its window is already full."""
        now = self._clock()
        if key not in self._hits and len(self._hits) >= self._max_keys:
            self._forget_idle(now)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._seconds:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True

    def _forget_idle(self, now: float) -> None:
        for key in [key for key, hits in self._hits.items() if hits[-1] <= now - self._seconds]:
            del self._hits[key]


def client_ip(request: Request, *, hops: int) -> str:
    """The address `hops` places from the right of X-Forwarded-For: one the
    platform in front of the service appended, which a client cannot forge.
    Entries further left came from the client itself."""
    header = request.headers.get("x-forwarded-for", "")
    forwarded = [part.strip() for part in header.split(",") if part.strip()]
    if len(forwarded) >= hops:
        return forwarded[-hops]
    return request.client.host if request.client else "unknown"
```

Replace `src/assistant/agent/budget.py` with:

```python
"""What the free tiers can bear (spec §7.5), checked before any model call.

The counts come from what the service already keeps: `agent_runs` holds every
turn's model requests and tokens, `messages` everything each sender wrote.
None switches a limit off.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from assistant.conversation.store import ConversationRecord, Store

type Spent = Literal["sender_rate", "daily_requests", "conversation_tokens"]

DAY = timedelta(days=1)

# Said once to a sender over the limit; after that, silence until they slow down.
SLOW_DOWN = (
    "Çok hızlı yazıyorsunuz, biraz yavaşlayalım. Birkaç dakika sonra yeniden yazabilirsiniz."
)


@dataclass(frozen=True)
class Budget:
    # Model requests in the last 24 hours, across every conversation: the
    # free tier's daily quota belongs to the whole service.
    daily_requests: int | None = None
    # Tokens one conversation may use in the last 24 hours.
    conversation_daily_tokens: int | None = None
    # Messages one sender may write in `sender_window`, in any conversation.
    sender_messages: int | None = None
    sender_window: timedelta = timedelta(minutes=10)

    async def spent(
        self, store: Store, conversation: ConversationRecord, now: datetime
    ) -> Spent | None:
        """Which limit this turn would cross, or None."""
        if self.sender_messages is not None:
            written = await store.inbound_since(
                conversation.channel, conversation.channel_user_id, now - self.sender_window
            )
            if written > self.sender_messages:
                return "sender_rate"
        if (
            self.daily_requests is not None
            and await store.requests_since(now - DAY) >= self.daily_requests
        ):
            return "daily_requests"
        if self.conversation_daily_tokens is not None:
            used = await store.tokens_since(conversation.id, now - DAY)
            if used >= self.conversation_daily_tokens:
                return "conversation_tokens"
        return None
```

In `src/assistant/conversation/store.py`, before `decisions`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -411,6 +411,39 @@ class Store:
             )
             return int(total.scalar_one())
 
+    async def inbound_since(
+        self, channel: ChannelName, channel_user_id: str, since: datetime
+    ) -> int:
+        """Messages one sender has written since `since`, in any conversation."""
+        async with self._sessions() as session:
+            count = await session.execute(
+                sa.select(sa.func.count())
+                .select_from(Message)
+                .join(Conversation, Conversation.id == Message.conversation_id)
+                .where(
+                    Conversation.channel == channel,
+                    Conversation.channel_user_id == channel_user_id,
+                    Message.direction == "in",
+                    Message.created_at >= since,
+                )
+            )
+            return count.scalar_one()
+
+    async def said_since(self, conversation_id: int, text: str, since: datetime) -> bool:
+        """Whether this conversation was told `text` since `since`."""
+        async with self._sessions() as session:
+            said = await session.execute(
+                sa.select(
+                    sa.exists().where(
+                        Message.conversation_id == conversation_id,
+                        Message.direction == "out",
+                        Message.text == text,
+                        Message.created_at >= since,
+                    )
+                )
+            )
+            return said.scalar_one()
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

In `src/assistant/conversation/turn.py`, the sender limit from the settings, and its answer:

```diff
--- a/src/assistant/conversation/turn.py
+++ b/src/assistant/conversation/turn.py
@@ -45,7 +45,7 @@ from assistant.agent.approvals import (
     write_actions,
     write_receipt,
 )
-from assistant.agent.budget import Budget
+from assistant.agent.budget import SLOW_DOWN, Budget
 from assistant.agent.handoff import booking_page, handoff_reply, unavailable_reply
 from assistant.channels.base import Channel, ChannelName
 from assistant.config import Settings
@@ -101,6 +101,8 @@ class TurnSettings:
             budget=Budget(
                 daily_requests=settings.daily_request_cap,
                 conversation_daily_tokens=settings.conversation_daily_token_cap,
+                sender_messages=settings.sender_message_limit,
+                sender_window=timedelta(seconds=settings.sender_window_seconds),
             ),
         )
 
@@ -270,7 +272,13 @@ class TurnProcessor:
         check_lease: Callable[[], None],
     ) -> TurnResult:
         turn = _Turn(processed=[message.seq for message in batch], started_at=self._clock())
-        spent = await self._settings.budget.spent(self._store, conversation.id, self._clock())
+        budget = self._settings.budget
+        spent = await budget.spent(self._store, conversation, self._clock())
+        if spent == "sender_rate":
+            # Spec §7.5: told once, then silence until the sender slows down.
+            since = self._clock() - budget.sender_window
+            told = await self._store.said_since(conversation.id, SLOW_DOWN, since)
+            return turn.finish([] if told else [Reply(SLOW_DOWN)], "degraded")
         if spent is not None:
             # No model call, and still an answer (spec §7.5).
             log.warning("Conversation %s: the %s budget is spent", conversation.id, spent)
```

In `src/assistant/config.py`:

```diff
--- a/src/assistant/config.py
+++ b/src/assistant/config.py
@@ -86,10 +86,13 @@ class Settings(DatabaseSettings):
     # Budgets (spec §7.5). None switches a cap off, as the evals do.
     daily_request_cap: int | None = 400
     conversation_daily_token_cap: int | None = 40_000
-    sender_message_limit: int = 20
+    sender_message_limit: int | None = 20
     sender_window_seconds: int = 600
     # The widget's posts per client IP, in the same window.
     ip_message_limit: int = 60
+    # Which X-Forwarded-For entry is the client, counted from the right: the
+    # ones the platform appends. Checked on Cloud Run before go-live.
+    trusted_proxy_hops: int = 1
 
     # Traces over OTLP/HTTP (spec §7.10): Langfuse's traces endpoint, and its
     # headers as "Authorization=Basic <base64 of public:secret key>".
```

In `src/assistant/services.py`:

```diff
--- a/src/assistant/services.py
+++ b/src/assistant/services.py
@@ -8,6 +8,7 @@ from fastapi import Request
 from assistant.config import Settings
 from assistant.conversation.store import Store
 from assistant.queue.base import TaskQueue
+from assistant.ratelimit import SlidingWindow
 
 
 @dataclass(frozen=True)
@@ -15,6 +16,8 @@ class AppServices:
     settings: Settings
     store: Store
     queue: TaskQueue
+    # The widget's posts per client address.
+    post_limit: SlidingWindow
 
 
 def app_services(request: Request) -> AppServices:
```

In `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -17,6 +17,7 @@ from assistant.conversation.turn import OpenRezerve, TurnProcessor, TurnSettings
 from assistant.db.session import create_engine, session_factory
 from assistant.mcp import connect
 from assistant.queue.local import LocalTaskQueue
+from assistant.ratelimit import SlidingWindow
 from assistant.services import AppServices
 
 
@@ -47,7 +48,12 @@ def create_app(
             settings=TurnSettings.from_settings(config),
         )
         queue = LocalTaskQueue(processor.process)
-        app.state.services = AppServices(settings=config, store=store, queue=queue)
+        post_limit = SlidingWindow(
+            limit=config.ip_message_limit, seconds=config.sender_window_seconds
+        )
+        app.state.services = AppServices(
+            settings=config, store=store, queue=queue, post_limit=post_limit
+        )
         try:
             yield
         finally:
```

In `src/assistant/channels/web.py`:

```diff
--- a/src/assistant/channels/web.py
+++ b/src/assistant/channels/web.py
@@ -11,11 +11,12 @@ from datetime import UTC, datetime
 from typing import Annotated, Literal
 from uuid import UUID
 
-from fastapi import APIRouter, Depends, Header, Path, Query
+from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
 from pydantic import BaseModel, StringConstraints
 
 from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
 from assistant.identity import customer_ref
+from assistant.ratelimit import client_ip
 from assistant.services import AppServices, app_services
 
 # Rezerve's own slug rule (lib/slug.ts).
@@ -65,7 +66,12 @@ class WebChannel:
 
 
 @router.post("/{slug}/messages", status_code=202)
-async def post_message(slug: Slug, body: PostedMessage, services: Services) -> Accepted:
+async def post_message(
+    slug: Slug, body: PostedMessage, services: Services, request: Request
+) -> Accepted:
+    address = client_ip(request, hops=services.settings.trusted_proxy_hops)
+    if not services.post_limit.allow(address):
+        raise HTTPException(status_code=429, detail="too_many_messages")
     message = InboundMessage(
         business_slug=slug,
         channel="web",
```

In `evals/config.py`, the sender limit off too:

```diff
--- a/evals/config.py
+++ b/evals/config.py
@@ -16,3 +16,4 @@ class EvalSettings(Settings):
     # spend more in a day than production's caps allow.
     daily_request_cap: int | None = None
     conversation_daily_token_cap: int | None = None
+    sender_message_limit: int | None = None
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/unit/test_ratelimit.py tests/integration/test_turn.py tests/integration/test_web.py tests/evals/test_run.py -q`
Expected: every test passes.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `441 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/ratelimit.py src/assistant/agent/budget.py src/assistant/conversation/store.py \
  src/assistant/conversation/turn.py src/assistant/config.py src/assistant/services.py \
  src/assistant/main.py src/assistant/channels/web.py evals/config.py \
  tests/unit/test_ratelimit.py tests/integration/test_turn.py tests/integration/test_web.py
git commit -m "feat(budget): a sender limit, and the widget's limit per client address

Spec §7.5: past 20 messages in 10 minutes a sender is told once to slow down,
then gets no answer until they do. Spec §5: the widget's posts are also counted
per client address, from the right of X-Forwarded-For, and a full window gets
a 429.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: Turns as Cloud Tasks

In production a turn is a named Cloud Task that calls the service back at `/internal/turn/{conversation}` (spec §4, §9). The call carries an OIDC token Cloud Tasks mints for the invoker service account, with the service's URL as its audience. Cloud Tasks refuses a task name it has seen in about the last hour, so a redelivered webhook asks for its turn once, as it does on the local queue. The client is behind a one-method protocol, so tests use a fake.

**Files:**
- Create: `src/assistant/queue/cloud_tasks.py`, `tests/fakes/tasks.py`
- Test: `tests/unit/test_cloud_tasks.py`
- Modify: `pyproject.toml` and `uv.lock`, through `uv add`

**Interfaces:**
- Produces: `TaskCreator`, a protocol with `async create_task(*, parent: str, task: tasks_v2.Task) -> tasks_v2.Task`. `tasks_v2.CloudTasksAsyncClient` satisfies it.
- Produces: `CloudTasksQueue(client, *, project, region, turns_queue, base_url, invoker)`, with `async enqueue_turn(conversation_id, message_seq)`. Task 10 adds `reminders_queue` and `enqueue_reminder`.
- Produces: `FakeTasks` in `tests/fakes/tasks.py`, which keeps `created: list[tuple[str, tasks_v2.Task]]` and refuses a name it has seen.

- [ ] **Step 1: Add the client library**

Run: `uv add google-cloud-tasks`
Expected: `pyproject.toml` lists `google-cloud-tasks>=2.26.0`, and `uv.lock` resolves google-cloud-tasks 2.26.0, under the `exclude-newer` pin.

- [ ] **Step 2: Write the failing tests**

Create `tests/fakes/tasks.py`:

```python
"""Cloud Tasks, stood in for: create_task, refusing a name it has seen."""

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2


class FakeTasks:
    def __init__(self) -> None:
        self.created: list[tuple[str, tasks_v2.Task]] = []

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
        if any(seen.name == task.name for _, seen in self.created):
            raise AlreadyExists(f"{task.name} exists")
        self.created.append((parent, task))
        return task
```

Create `tests/unit/test_cloud_tasks.py`:

```python
from google.cloud import tasks_v2

from assistant.queue.cloud_tasks import CloudTasksQueue
from tests.fakes.tasks import FakeTasks

INVOKER = "assistant-tasks@rezerve-assistant.iam.gserviceaccount.com"


def tasks_queue(fake: FakeTasks) -> CloudTasksQueue:
    return CloudTasksQueue(
        fake,
        project="rezerve-assistant",
        region="europe-west1",
        turns_queue="turns",
        base_url="https://assistant.example/",
        invoker=INVOKER,
    )


async def test_a_turn_is_a_named_task_calling_the_service_back_with_a_token_for_it() -> None:
    fake = FakeTasks()
    await tasks_queue(fake).enqueue_turn(7, 42)

    [(parent, task)] = fake.created
    assert parent == "projects/rezerve-assistant/locations/europe-west1/queues/turns"
    assert task.name == f"{parent}/tasks/turn-7-42"
    assert task.http_request.url == "https://assistant.example/internal/turn/7"
    assert task.http_request.http_method == tasks_v2.HttpMethod.POST
    assert task.http_request.oidc_token.service_account_email == INVOKER
    assert task.http_request.oidc_token.audience == "https://assistant.example"


async def test_asking_twice_for_one_turn_queues_it_once() -> None:
    # A redelivered webhook asks again under the same name; Cloud Tasks
    # refuses it, and that is not an error.
    fake = FakeTasks()
    queue = tasks_queue(fake)
    await queue.enqueue_turn(7, 42)
    await queue.enqueue_turn(7, 42)
    assert len(fake.created) == 1
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/unit/test_cloud_tasks.py -q`
Expected: collection fails: `ModuleNotFoundError: No module named 'assistant.queue.cloud_tasks'`.

- [ ] **Step 4: Write the queue**

Create `src/assistant/queue/cloud_tasks.py`:

```python
"""Cloud Tasks (spec §4, §9). A turn is a named task that calls this service
back at /internal/turn/{conversation}, with an OIDC token Cloud Tasks mints
for the invoker service account and this service's URL as its audience.

Cloud Tasks refuses a task name it has seen in about the last hour, so a
redelivered webhook asks for its turn once, as it does on the local queue.
"""

import logging
from typing import Protocol

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2

log = logging.getLogger(__name__)

Client = tasks_v2.CloudTasksAsyncClient


class TaskCreator(Protocol):
    """The one Cloud Tasks call this service makes."""

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task: ...


class CloudTasksQueue:
    def __init__(
        self,
        client: TaskCreator,
        *,
        project: str,
        region: str,
        turns_queue: str,
        base_url: str,
        invoker: str,
    ) -> None:
        self._client = client
        self._project = project
        self._region = region
        self._turns = turns_queue
        self._base_url = base_url.rstrip("/")
        self._invoker = invoker

    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        await self._create(
            self._turns,
            f"turn-{conversation_id}-{message_seq}",
            f"/internal/turn/{conversation_id}",
        )

    async def _create(self, queue: str, name: str, path: str) -> None:
        parent = Client.queue_path(self._project, self._region, queue)
        task = tasks_v2.Task(
            name=Client.task_path(self._project, self._region, queue, name),
            http_request=tasks_v2.HttpRequest(
                url=f"{self._base_url}{path}",
                http_method=tasks_v2.HttpMethod.POST,
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._invoker, audience=self._base_url
                ),
            ),
        )
        try:
            await self._client.create_task(parent=parent, task=task)
        except AlreadyExists:
            log.info("Task %s is already queued", name)
```

- [ ] **Step 5: Run them to see them pass**

Run: `uv run pytest tests/unit/test_cloud_tasks.py -q`
Expected: `2 passed`.

- [ ] **Step 6: Run every check**

Run, reading each output:
- `uv run pytest -q`: `443 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/assistant/queue/cloud_tasks.py tests/fakes/tasks.py \
  tests/unit/test_cloud_tasks.py
git commit -m "feat(queue): turns as named Cloud Tasks, calling the service back

Each turn is a task named turn-<conversation>-<message> that calls
/internal/turn/<conversation> with an OIDC token for the invoker service
account. A name Cloud Tasks has seen is refused, and that is not an error.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The call-back, behind Google's token check

The app picks its queue from `QUEUE_BACKEND`. On Cloud Tasks, `/internal/turn/{conversation}` runs the turn, but only for a bearer token that Google signed for `TASKS_INVOKER_SA`, with `PUBLIC_BASE_URL` as its audience.
- **A missing, invalid or foreign token** gets a 401.
- **A valid token for another account** gets a 403.
- **A conversation another handler holds** answers 429, and Cloud Tasks retries it.

The routes are always mounted. On the local queue there is no invoker account, so they refuse everyone there too.

Google's check fetches Google's public keys, so it runs in a thread. Its errors map to:
- a bad token (`ValueError`) or a wrong issuer (`GoogleAuthError`): 401;
- keys out of reach (`TransportError`): a 500, which Cloud Tasks retries.

**Files:**
- Create: `src/assistant/internal.py`
- Modify: `src/assistant/services.py`, `src/assistant/main.py` (whole files below), and `pyproject.toml` and `uv.lock`, through `uv add`
- Test: `tests/unit/test_internal.py`, `tests/integration/test_internal.py`

**Interfaces:**
- Consumes: `CloudTasksQueue` and `TaskCreator` (Task 4); `SlidingWindow` (Task 3).
- Produces: `TokenVerifier`, in `services.py`: `Callable[[str, str], Awaitable[Mapping[str, Any]]]`, taking a token and an audience and returning claims.
- Produces: `InvalidToken`, `verify_google_token(token, audience)`, and `router`, in `assistant.internal`.
- Produces: `AppServices.process_turn: Callable[[int], Awaitable[None]]` and `AppServices.verify_token: TokenVerifier`.
- Produces: `create_app(settings=None, *, model=None, open_rezerve=None, tasks_client: TaskCreator | None = None, verify_token: TokenVerifier | None = None)`. Its lifespan holds every resource in one `AsyncExitStack`.

- [ ] **Step 1: Add Google's auth library**

Run: `uv add google-auth`
Expected: `pyproject.toml` lists `google-auth>=2.62.0`, resolved to google-auth 2.62.0.

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_internal.py`:

```python
from collections.abc import Mapping
from typing import Any

import pytest
from google.auth.exceptions import GoogleAuthError, TransportError
from google.oauth2 import id_token

from assistant.internal import InvalidToken, verify_google_token


async def test_google_checks_the_token_for_this_service(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def verify(token: str, request: object, audience: str) -> Mapping[str, Any]:
        seen.append((token, audience))
        return {"email": "tasks@p.iam.gserviceaccount.com", "email_verified": True}

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)
    claims = await verify_google_token("t0ken", "https://assistant.example")
    assert claims["email"] == "tasks@p.iam.gserviceaccount.com"
    assert seen == [("t0ken", "https://assistant.example")]


@pytest.mark.parametrize("error", [ValueError("Token expired"), GoogleAuthError("Wrong issuer")])
async def test_a_token_google_rejects_is_invalid(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def verify(token: str, request: object, audience: str) -> Mapping[str, Any]:
        raise error

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)
    with pytest.raises(InvalidToken):
        await verify_google_token("t0ken", "https://assistant.example")


async def test_googles_keys_out_of_reach_is_a_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Not the caller's fault: a 500, which Cloud Tasks retries.
    def verify(token: str, request: object, audience: str) -> Mapping[str, Any]:
        raise TransportError("certs unreachable")

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)
    with pytest.raises(TransportError):
        await verify_google_token("t0ken", "https://assistant.example")
```

Create `tests/integration/test_internal.py`:

```python
"""Cloud Tasks' side of the service, in process: a widget post queues a named
task, and that task's call back to /internal/turn answers the turn, but only
with a token Google signed for the invoker account (spec §5)."""

from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.internal import InvalidToken
from assistant.main import create_app
from tests.fakes.models import Script, says
from tests.fakes.rezerve import FakeRezerve
from tests.fakes.tasks import FakeTasks

BASE = "https://assistant.example"
INVOKER = "assistant-tasks@rezerve-assistant.iam.gserviceaccount.com"
TOKENS = {"invoker-token": INVOKER, "stranger-token": "someone@else.iam.gserviceaccount.com"}


async def verify(token: str, audience: str) -> Mapping[str, Any]:
    """Google's check, stood in for: one token per account the tests need."""
    if audience != BASE or token not in TOKENS:
        raise InvalidToken("Could not verify token")
    return {"email": TOKENS[token], "email_verified": True, "aud": audience}


class Service:
    def __init__(self, client: httpx.AsyncClient, tasks: FakeTasks, script: Script) -> None:
        self.client = client
        self.tasks = tasks
        self.script = script
        self.visitor_id = str(uuid4())

    async def post(self, text: str) -> None:
        body = {"visitor_id": self.visitor_id, "client_message_id": str(uuid4()), "text": text}
        response = await self.client.post("/web/demo/messages", json=body)
        assert response.status_code == 202

    def last_task_path(self) -> str:
        """Where the last task calls back, as a path on this service."""
        _, task = self.tasks.created[-1]
        return task.http_request.url.removeprefix(BASE)

    async def call_back(self, path: str, token: str | None = "invoker-token") -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await self.client.post(path, headers=headers)

    async def replies(self) -> list[str]:
        response = await self.client.get(
            "/web/demo/messages", headers={"X-Visitor-Id": self.visitor_id}
        )
        return [m["text"] for m in response.json()["messages"] if m["direction"] == "out"]


@pytest.fixture
async def service(database_url: str, store: Store) -> AsyncIterator[Service]:
    fake = FakeRezerve()
    tasks = FakeTasks()
    script = Script()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        public_base_url=BASE,
        queue_backend="cloud_tasks",
        gcp_project="rezerve-assistant",
        tasks_invoker_sa=INVOKER,
    )
    app = create_app(
        settings,
        model=script.model,
        open_rezerve=lambda slug, ref: fake.session(),
        tasks_client=tasks,
        verify_token=verify,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Service(client, tasks, script)


async def test_a_message_queues_a_task_whose_call_back_answers_it(service: Service) -> None:
    service.script.add(says("Merhaba! Size nasıl yardımcı olabilirim?"))
    await service.post("merhaba")
    path = service.last_task_path()
    assert path.startswith("/internal/turn/")
    assert await service.replies() == []

    response = await service.call_back(path)

    assert response.status_code == 204
    assert await service.replies() == ["Merhaba! Size nasıl yardımcı olabilirim?"]


@pytest.mark.parametrize(
    ("authorization", "status"),
    [(None, 401), ("forged", 401), ("stranger-token", 403)],
)
async def test_only_the_invoker_account_may_start_a_turn(
    service: Service, authorization: str | None, status: int
) -> None:
    await service.post("merhaba")
    response = await service.call_back(service.last_task_path(), token=authorization)
    assert response.status_code == status
    assert await service.replies() == []


async def test_a_turn_another_handler_holds_is_tried_again_later(
    service: Service, store: Store
) -> None:
    await service.post("merhaba")
    path = service.last_task_path()
    conversation_id = int(path.rsplit("/", 1)[1])
    assert await store.claim_lease(conversation_id, "another-handler", timedelta(minutes=1))

    response = await service.call_back(path)

    # 429: Cloud Tasks retries it with backoff (spec §7.2).
    assert response.status_code == 429
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/unit/test_internal.py tests/integration/test_internal.py -q`
Expected: both fail to collect: `ModuleNotFoundError: No module named 'assistant.internal'`.

- [ ] **Step 4: Write the endpoint and pick the queue**

Create `src/assistant/internal.py`:

```python
"""Calls from Cloud Tasks (spec §5). Each carries an OIDC token Google signed
for the invoker service account, with this service's URL as its audience;
anything else is refused before a turn can start.

The routes are always mounted. A service on the local queue has no invoker
account, so every call to them is refused there too.
"""

import asyncio
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from assistant.conversation.lease import LeaseBusy
from assistant.services import AppServices, app_services

type Services = Annotated[AppServices, Depends(app_services)]

router = APIRouter(prefix="/internal", include_in_schema=False)

_google = GoogleRequest()


class InvalidToken(Exception):
    """Not a current Google token for this service."""


def _verify(token: str, audience: str) -> Mapping[str, Any]:
    # google-auth leaves this untyped; it returns the token's claims.
    claims: Mapping[str, Any] = id_token.verify_oauth2_token(  # pyright: ignore[reportUnknownMemberType]
        token, _google, audience
    )
    return claims


async def verify_google_token(token: str, audience: str) -> Mapping[str, Any]:
    """Google's own check of signature, issuer, expiry and audience. It fetches
    Google's public keys, so it runs off the event loop."""
    try:
        return await asyncio.to_thread(_verify, token, audience)
    except TransportError:
        # Google's keys out of reach is no fault of the caller's: a 500, retried.
        raise
    except (ValueError, GoogleAuthError) as error:
        raise InvalidToken(str(error)) from error


async def from_cloud_tasks(request: Request, services: Services) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    audience = services.settings.public_base_url.rstrip("/")
    try:
        claims = await services.verify_token(token, audience)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="unauthorized") from None
    invoker = services.settings.tasks_invoker_sa
    if (
        invoker is None
        or claims.get("email") != invoker
        or claims.get("email_verified") is not True
    ):
        raise HTTPException(status_code=403, detail="forbidden")


@router.post("/turn/{conversation_id}", status_code=204, dependencies=[Depends(from_cloud_tasks)])
async def turn(conversation_id: int, services: Services) -> Response:
    try:
        await services.process_turn(conversation_id)
    except LeaseBusy:
        # Another handler has the conversation: Cloud Tasks tries again later.
        raise HTTPException(status_code=429, detail="busy") from None
    return Response(status_code=204)
```

Replace `src/assistant/services.py` with:

```python
"""What the HTTP layer reaches for: built once in the app's lifespan."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request

from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.queue.base import TaskQueue
from assistant.ratelimit import SlidingWindow

# Checks a bearer token for an audience and returns its claims (spec §5).
type TokenVerifier = Callable[[str, str], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    store: Store
    queue: TaskQueue
    # The widget's posts per client address.
    post_limit: SlidingWindow
    # Answers a conversation's unprocessed messages: what a turn task runs.
    process_turn: Callable[[int], Awaitable[None]]
    verify_token: TokenVerifier


def app_services(request: Request) -> AppServices:
    return cast(AppServices, request.app.state.services)
```

Replace `src/assistant/main.py` with:

```python
"""The service: `uvicorn assistant.main:create_app --factory`."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import tasks_v2
from pydantic_ai.models import Model

from assistant import internal
from assistant.agent.agent import build_agent, build_model
from assistant.agent.approvals import ReplyClassifier
from assistant.channels import web
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.conversation.turn import OpenRezerve, TurnProcessor, TurnSettings
from assistant.db.session import create_engine, session_factory
from assistant.mcp import connect
from assistant.queue.base import TaskQueue
from assistant.queue.cloud_tasks import CloudTasksQueue, TaskCreator
from assistant.queue.local import LocalTaskQueue
from assistant.ratelimit import SlidingWindow
from assistant.services import AppServices, TokenVerifier


def create_app(
    settings: Settings | None = None,
    *,
    model: Model | None = None,
    open_rezerve: OpenRezerve | None = None,
    tasks_client: TaskCreator | None = None,
    verify_token: TokenVerifier | None = None,
) -> FastAPI:
    """Tests pass a model, a Rezerve, Cloud Tasks and a token check of their
    own; production builds each from settings."""
    config = settings or Settings()  # pyright: ignore[reportCallIssue]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            engine = create_engine(config.database_url)
            stack.push_async_callback(engine.dispose)
            store = Store(session_factory(engine))
            chosen = model or build_model(config)
            processor = TurnProcessor(
                store=store,
                agent=build_agent(chosen),
                classifier=ReplyClassifier(chosen),
                open_rezerve=open_rezerve
                or partial(
                    connect, config.rezerve_mcp_url, config.assistant_mcp_token.get_secret_value()
                ),
                channels={"web": web.WebChannel()},
                settings=TurnSettings.from_settings(config),
            )
            queue: TaskQueue
            if config.queue_backend == "cloud_tasks":
                if config.gcp_project is None or config.tasks_invoker_sa is None:
                    raise RuntimeError("Settings let cloud_tasks through without its project")
                client = tasks_client or await stack.enter_async_context(
                    tasks_v2.CloudTasksAsyncClient()
                )
                queue = CloudTasksQueue(
                    client,
                    project=config.gcp_project,
                    region=config.gcp_region,
                    turns_queue=config.tasks_queue_turns,
                    base_url=config.public_base_url,
                    invoker=config.tasks_invoker_sa,
                )
            else:
                local = LocalTaskQueue(processor.process)
                stack.push_async_callback(local.aclose)
                queue = local
            app.state.services = AppServices(
                settings=config,
                store=store,
                queue=queue,
                post_limit=SlidingWindow(
                    limit=config.ip_message_limit, seconds=config.sender_window_seconds
                ),
                process_turn=processor.process,
                verify_token=verify_token or internal.verify_google_token,
            )
            yield

    app = FastAPI(title="Rezerve Assistant", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_web_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Visitor-Id"],
        # The widget polls every second; one preflight per ten minutes is plenty.
        max_age=600,
    )
    app.include_router(web.router)
    app.include_router(internal.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 5: Run them to see them pass**

Run: `uv run pytest tests/unit/test_internal.py tests/integration/test_internal.py tests/integration/test_web.py -q`
Expected: every test passes.

- [ ] **Step 6: Run every check**

Run, reading each output:
- `uv run pytest -q`: `452 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`. google-auth's verifier is untyped, so `_verify` wraps it with one targeted ignore.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/assistant/internal.py src/assistant/services.py \
  src/assistant/main.py tests/unit/test_internal.py tests/integration/test_internal.py
git commit -m "feat(queue): Cloud Tasks call back to /internal/turn, behind Google's token check

The app picks its queue from QUEUE_BACKEND. A call-back runs the turn only for
an OIDC token Google signed for the invoker account, with the service URL as
its audience: 401 or 403 otherwise, 429 while another handler holds it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Telegram's Bot API, and the channel

One bot serves every business (spec §7.9).
- **Sending:** a reply goes out with `sendMessage`, and a link, such as the kapora's payment page, goes as an inline URL button.
- **A chat that blocked the bot, or is gone:** a 403 or a 400 is marked as undelivered rather than raised. A retry would never deliver it, and would hold up every reply after it.
- **A passing failure:** a 429, a 5xx or a network error is raised, so the turn is retried.

The bot token is part of every Bot API URL. So no error message carries the URL, and httpx's own request log is silenced.

**Files:**
- Create: `src/assistant/channels/telegram.py`, `tests/fakes/telegram.py`
- Modify: `pyproject.toml` and `uv.lock`, through `uv add`
- Test: `tests/unit/test_telegram_channel.py`

**Interfaces:**
- Produces: `TelegramApi(token: str, client: httpx.AsyncClient)`, with `async send_message(chat_id: int, text: str, buttons: Sequence[LinkAction] = ()) -> int`, which returns the message id.
- Produces: `TelegramError(status: int, description: str)`, with `.status`. Status 0 means a network failure.
- Produces: `TelegramChannel(api)`, named `"telegram"`. Its `send(channel_user_id, message)` returns `"telegram:<message id>"`, or `"telegram:undelivered:<status>"`.
- Produces: `FakeTelegram`, with:
  - `.transport` (an `httpx.MockTransport`);
  - `.calls`, every call as `(method, payload)`;
  - `.sent()`, every sendMessage payload;
  - `.texts(chat_id)`;
  - `.fail_with`, an error to answer every call with.

- [ ] **Step 1: Declare httpx, which the service now imports itself**

Run: `uv add httpx`
Expected: `pyproject.toml` lists `httpx>=0.28.1` among the service's dependencies. Until now it came only through Pydantic AI, and as a dev dependency. The locked version, 0.28.1, doesn't change.

- [ ] **Step 2: Write the failing tests**

Create `tests/fakes/telegram.py`:

```python
"""Telegram's Bot API, stood in for over httpx: every call is kept, and
sendMessage answers the way Telegram does, unless told to fail."""

import json
from typing import Any

import httpx


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id = 100
        # An error Telegram returns to every call from now on, as (status, description).
        self.fail_with: tuple[int, str] | None = None

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload: dict[str, Any] = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if self.fail_with is not None:
            status, description = self.fail_with
            return httpx.Response(
                status, json={"ok": False, "error_code": status, "description": description}
            )
        self._next_id += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_id}})

    def sent(self) -> list[dict[str, Any]]:
        """Every sendMessage, in order."""
        return [payload for method, payload in self.calls if method == "sendMessage"]

    def texts(self, chat_id: int) -> list[str]:
        return [message["text"] for message in self.sent() if message["chat_id"] == chat_id]
```

Create `tests/unit/test_telegram_channel.py`:

```python
import httpx
import pytest

from assistant.channels.base import OutboundMessage
from assistant.channels.telegram import TelegramApi, TelegramChannel, TelegramError
from tests.fakes.telegram import FakeTelegram

TOKEN = "123456:secret-token"


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def api(telegram: FakeTelegram) -> TelegramApi:
    return TelegramApi(TOKEN, httpx.AsyncClient(transport=telegram.transport))


async def test_a_reply_is_sent_to_the_chat_and_its_id_kept(
    api: TelegramApi, telegram: FakeTelegram
) -> None:
    sent = await TelegramChannel(api).send("42", OutboundMessage(seq=7, text="Merhaba!"))
    assert sent == "telegram:101"
    assert telegram.sent() == [{"chat_id": 42, "text": "Merhaba!"}]


async def test_the_payment_link_is_a_button(api: TelegramApi, telegram: FakeTelegram) -> None:
    message = OutboundMessage(
        seq=8,
        text="Randevunuz ayrıldı.",
        actions=[{"type": "link", "label": "Kaporayı öde", "url": "https://pay.example/x"}],
    )
    await TelegramChannel(api).send("42", message)
    [payload] = telegram.sent()
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Kaporayı öde", "url": "https://pay.example/x"}]]
    }


@pytest.mark.parametrize(
    ("status", "description"),
    [(403, "Forbidden: bot was blocked by the user"), (400, "Bad Request: chat not found")],
)
async def test_a_chat_that_cannot_be_reached_is_given_up_on(
    api: TelegramApi, telegram: FakeTelegram, status: int, description: str
) -> None:
    # Retrying would never deliver it, and would hold up every reply after it.
    telegram.fail_with = (status, description)
    sent = await TelegramChannel(api).send("42", OutboundMessage(seq=7, text="Merhaba!"))
    assert sent == f"telegram:undelivered:{status}"


@pytest.mark.parametrize("status", [429, 502])
async def test_a_passing_failure_is_raised_so_the_turn_is_retried(
    api: TelegramApi, telegram: FakeTelegram, status: int
) -> None:
    telegram.fail_with = (status, "Too Many Requests: retry after 3")
    with pytest.raises(TelegramError):
        await TelegramChannel(api).send("42", OutboundMessage(seq=7, text="Merhaba!"))


async def test_the_bot_token_is_in_no_error_message(
    api: TelegramApi, telegram: FakeTelegram
) -> None:
    # The token is in Telegram's URLs; error messages end up in logs.
    telegram.fail_with = (502, "Bad Gateway")
    with pytest.raises(TelegramError) as raised:
        await api.send_message(42, "Merhaba!")
    assert "secret-token" not in str(raised.value)


async def test_a_network_failure_names_no_token_either() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    api = TelegramApi(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(TelegramError) as raised:
        await api.send_message(42, "Merhaba!")
    assert "secret-token" not in str(raised.value)
    assert raised.value.__suppress_context__
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/unit/test_telegram_channel.py -q`
Expected: collection fails: `ModuleNotFoundError: No module named 'assistant.channels.telegram'`.

- [ ] **Step 4: Write the client and the channel**

Create `src/assistant/channels/telegram.py`:

```python
"""Telegram (spec §7.9): one bot for every business.

The bot token is part of every Bot API URL, so no URL of this module's is
logged or traced, and no error message carries one.
"""

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from assistant.channels.base import ChannelName, LinkAction, OutboundMessage

log = logging.getLogger(__name__)

# httpx logs each request's URL at INFO, and Telegram's URLs hold the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.telegram.org"
# Answers that retrying will never change: the chat is gone or blocked us.
UNREACHABLE = frozenset({400, 403})


class TelegramError(Exception):
    def __init__(self, status: int, description: str) -> None:
        super().__init__(f"Telegram refused the call: {status} {description}")
        self.status = status


class TelegramApi:
    """The Bot API calls this service makes."""

    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._base = f"{API}/bot{token}"
        self._client = client

    async def send_message(
        self, chat_id: int, text: str, buttons: Sequence[LinkAction] = ()
    ) -> int:
        """Send `text`, with each link as an inline button; returns its message id."""
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": link["label"], "url": link["url"]}] for link in buttons
                ]
            }
        return int((await self._call("sendMessage", payload))["message_id"])

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as error:
            # httpx's message would name the URL, and with it the token.
            raise TelegramError(0, type(error).__name__) from None
        body: dict[str, Any] = response.json()
        if not body.get("ok"):
            status = int(body.get("error_code") or response.status_code)
            raise TelegramError(status, str(body.get("description", "")))
        result: dict[str, Any] = body["result"]
        return result


class TelegramChannel:
    name: ChannelName = "telegram"

    def __init__(self, api: TelegramApi) -> None:
        self._api = api

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        try:
            sent = await self._api.send_message(int(channel_user_id), message.text, message.actions)
        except TelegramError as error:
            if error.status not in UNREACHABLE:
                raise
            # Marked sent all the same: the replies after it must not wait on it.
            log.warning("Telegram chat %s unreachable: %s", channel_user_id, error)
            return f"telegram:undelivered:{error.status}"
        return f"telegram:{sent}"
```

- [ ] **Step 5: Run them to see them pass**

Run: `uv run pytest tests/unit/test_telegram_channel.py -q`
Expected: `8 passed`.

- [ ] **Step 6: Run every check**

Run, reading each output:
- `uv run pytest -q`: `460 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/assistant/channels/telegram.py tests/fakes/telegram.py \
  tests/unit/test_telegram_channel.py
git commit -m "feat(telegram): the Bot API client and the channel

Replies go out with sendMessage, links as inline URL buttons. A chat that blocked
the bot is marked undelivered instead of retried; a 429 or 5xx is raised so the
turn is retried. The bot token is in no log line and no error message.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Telegram's webhook

Telegram posts each update to `/telegram/webhook`, with the secret set at `setWebhook` in `X-Telegram-Bot-Api-Secret-Token`. The secret is compared in constant time, and a wrong one gets a 401. Without a bot token or a webhook secret, the route answers 404. Within a private chat:
- **The booking page's deep link,** `t.me/<bot>?start=<slug>`, arrives as `/start <slug>`. It binds the chat to that business (a new table, `telegram_chats`), and the bot greets.
- **Text from a bound chat** is stored with the update id, unique across the bot, so a redelivery is stored once (spec §7.2). Then its turn is queued.
- **A chat that never came through a business's link** is told where to begin.
- **Photos and stickers** get "text only".
- **Group chats** are ignored.

**Files:**
- Create: `src/assistant/channels/telegram_webhook.py`, `src/assistant/db/alembic/versions/0005_telegram_chats.py`
- Modify: `src/assistant/db/models.py`, `src/assistant/conversation/store.py`, `src/assistant/services.py`, `src/assistant/main.py`, `tests/conftest.py`
- Test: `tests/integration/test_telegram.py`

**Interfaces:**
- Consumes: `TelegramApi` and `TelegramChannel` (Task 6); `customer_ref` with channel `"telegram"`.
- Produces: `WELCOME`, `NO_BUSINESS`, `TEXT_ONLY` and `router`, in `assistant.channels.telegram_webhook`.
- Produces: `Store.bind_telegram_chat(chat_id: int, business_slug: str, now)` and `Store.telegram_business(chat_id: int) -> str | None`.
- Produces: `AppServices.telegram: TelegramApi | None`.
- Produces: `create_app(..., telegram_transport: httpx.AsyncBaseTransport | None = None)`. The app registers `"telegram"` among the processor's channels when `TELEGRAM_BOT_TOKEN` is set.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_telegram.py`:

```python
"""The Telegram webhook, through the whole service in process: the app and
its lifespan, the local queue, Postgres, the fake Rezerve, a scripted model,
and a fake Bot API."""

from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from assistant.channels.telegram_webhook import NO_BUSINESS, TEXT_ONLY, WELCOME
from assistant.config import Settings
from assistant.conversation.store import Store
from assistant.main import create_app
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices
from tests.fakes.models import Script, says
from tests.fakes.rezerve import FakeRezerve
from tests.fakes.telegram import FakeTelegram

SECRET = "webhook-secret"
CHAT = 4242


class Bot:
    """Telegram's side of the webhook: updates, posted the way the Bot API does."""

    def __init__(
        self, app: FastAPI, client: httpx.AsyncClient, telegram: FakeTelegram, script: Script
    ) -> None:
        self.app = app
        self.client = client
        self.telegram = telegram
        self.script = script
        self._update_id = 1000

    async def deliver(
        self, message: dict[str, Any], *, secret: str | None = SECRET, update_id: int | None = None
    ) -> httpx.Response:
        if update_id is None:
            self._update_id += 1
            update_id = self._update_id
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
        return await self.client.post(
            "/telegram/webhook", json={"update_id": update_id, "message": message}, headers=headers
        )

    async def write(
        self,
        text: str,
        *,
        chat_type: str = "private",
        secret: str | None = SECRET,
        update_id: int | None = None,
    ) -> httpx.Response:
        message = {
            "message_id": 1,
            "from": {"id": CHAT, "is_bot": False, "first_name": "Elif"},
            "chat": {"id": CHAT, "type": chat_type},
            "date": 1_790_000_000,
            "text": text,
        }
        return await self.deliver(message, secret=secret, update_id=update_id)

    async def settle(self) -> None:
        services = cast(AppServices, self.app.state.services)
        await cast(LocalTaskQueue, services.queue).drain()


@pytest.fixture
def bot_overrides() -> dict[str, Any]:
    """Settings a test changes, by parametrizing this fixture's name."""
    return {}


@pytest.fixture
async def bot(database_url: str, store: Store, bot_overrides: dict[str, Any]) -> AsyncIterator[Bot]:
    fake = FakeRezerve()
    telegram = FakeTelegram()
    script = Script()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        **{"telegram_bot_token": "123456:secret-token", "telegram_webhook_secret": SECRET}
        | bot_overrides,
    )
    app = create_app(
        settings,
        model=script.model,
        open_rezerve=lambda slug, ref: fake.session(),
        telegram_transport=telegram.transport,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Bot(app, client, telegram, script)


async def test_the_start_link_binds_the_chat_to_its_business(bot: Bot, store: Store) -> None:
    # t.me/<bot>?start=demo arrives as "/start demo" (spec §7.9).
    response = await bot.write("/start demo")
    assert response.status_code == 200
    assert bot.telegram.texts(CHAT) == [WELCOME]
    assert await store.telegram_business(CHAT) == "demo"


async def test_a_message_after_the_start_link_is_answered_in_the_chat(bot: Bot) -> None:
    bot.script.add(says("Merhaba! Yarın için hangi hizmete bakalım?"))
    await bot.write("/start demo")
    await bot.write("yarın yer var mı?")
    await bot.settle()
    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba! Yarın için hangi hizmete bakalım?"]


async def test_a_redelivered_update_is_answered_once(bot: Bot) -> None:
    # Telegram resends an update it did not see acknowledged; update_id is
    # unique across the bot, a message_id only within its chat (spec §7.2).
    bot.script.add(says("Merhaba!"))
    await bot.write("/start demo")
    await bot.write("merhaba", update_id=5000)
    await bot.write("merhaba", update_id=5000)
    await bot.settle()
    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba!"]


async def test_a_chat_that_never_started_from_a_business_is_told_where_to_begin(
    bot: Bot, store: Store
) -> None:
    for text in ("merhaba", "/start", "/start Not A Slug!"):
        await bot.write(text)
    assert bot.telegram.texts(CHAT) == [NO_BUSINESS] * 3
    assert await store.telegram_business(CHAT) is None


async def test_only_written_messages_are_understood(bot: Bot) -> None:
    await bot.write("/start demo")
    sticker = {
        "message_id": 2,
        "chat": {"id": CHAT, "type": "private"},
        "date": 1_790_000_000,
        "sticker": {"file_id": "x", "width": 512, "height": 512},
    }
    await bot.deliver(sticker)
    assert bot.telegram.texts(CHAT) == [WELCOME, TEXT_ONLY]


async def test_group_chats_are_not_conversations(bot: Bot) -> None:
    response = await bot.write("/start demo", chat_type="group")
    assert response.status_code == 200
    assert bot.telegram.calls == []


@pytest.mark.parametrize("secret", [None, "guessed"])
async def test_only_telegram_knows_the_webhook_secret(bot: Bot, secret: str | None) -> None:
    response = await bot.write("/start demo", secret=secret)
    assert response.status_code == 401
    assert bot.telegram.calls == []


@pytest.mark.parametrize("bot_overrides", [{"telegram_bot_token": None}])
async def test_without_a_bot_there_is_no_webhook(bot: Bot) -> None:
    assert (await bot.write("/start demo")).status_code == 404
```

In `tests/conftest.py`, the new table is emptied with the others:

```diff
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -32,8 +32,8 @@ async def store(database_url: str) -> AsyncIterator[Store]:
     async with engine.begin() as connection:
         await connection.execute(
             sa.text(
-                "TRUNCATE conversations, messages, pending_approvals, agent_runs "
-                "RESTART IDENTITY CASCADE"
+                "TRUNCATE conversations, messages, pending_approvals, agent_runs, "
+                "telegram_chats RESTART IDENTITY CASCADE"
             )
         )
     yield Store(session_factory(engine))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/integration/test_telegram.py -q`
Expected: collection fails: `ModuleNotFoundError: No module named 'assistant.channels.telegram_webhook'`.

- [ ] **Step 3: Write the binding table, the webhook and the wiring**

Create `src/assistant/db/alembic/versions/0005_telegram_chats.py`:

```python
"""Which business each Telegram chat talks to.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_chats",
        sa.Column("chat_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("business_slug", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("telegram_chats")
```

In `src/assistant/db/models.py`:

```diff
--- a/src/assistant/db/models.py
+++ b/src/assistant/db/models.py
@@ -156,3 +156,13 @@ class AgentRun(Base):
         sa.Index("agent_runs_started_at", "started_at"),
         sa.Index("agent_runs_conversation_started_at", "conversation_id", "started_at"),
     )
+
+
+class TelegramChat(Base):
+    """Which business a Telegram chat talks to: the one its last /start named."""
+
+    __tablename__ = "telegram_chats"
+
+    chat_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
+    business_slug: Mapped[str] = mapped_column(sa.Text)
+    updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
```

In `src/assistant/conversation/store.py`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -13,7 +13,7 @@ from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
 
 from assistant.channels.base import Action, ChannelName, InboundMessage, OutboundMessage
 from assistant.conversation import history
-from assistant.db.models import AgentRun, Conversation, Message, PendingApproval
+from assistant.db.models import AgentRun, Conversation, Message, PendingApproval, TelegramChat
 
 # A conversation idle this long is closed; the next message opens a new one.
 IDLE_CLOSE = timedelta(hours=24)
@@ -444,6 +444,24 @@ class Store:
             )
             return said.scalar_one()
 
+    async def bind_telegram_chat(self, chat_id: int, business_slug: str, now: datetime) -> None:
+        """From now on the chat talks to this business (a later /start switches it)."""
+        async with self._sessions.begin() as session:
+            await session.execute(
+                pg_insert(TelegramChat)
+                .values(chat_id=chat_id, business_slug=business_slug, updated_at=now)
+                .on_conflict_do_update(
+                    index_elements=["chat_id"],
+                    set_={"business_slug": business_slug, "updated_at": now},
+                )
+            )
+
+    async def telegram_business(self, chat_id: int) -> str | None:
+        async with self._sessions() as session:
+            return await session.scalar(
+                sa.select(TelegramChat.business_slug).where(TelegramChat.chat_id == chat_id)
+            )
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

Create `src/assistant/channels/telegram_webhook.py`:

```python
"""Telegram's webhook (spec §7.2, §7.9): it checks the secret Telegram sends
with every update, stores the customer's message once, and asks for a turn.

The deep link t.me/<bot>?start=<slug> on a business's booking page arrives as
"/start <slug>" and binds the chat to that business; a later one switches it.
"""

import hmac
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from assistant.channels.base import InboundMessage
from assistant.identity import customer_ref
from assistant.services import AppServices, app_services

WELCOME = (
    "Merhaba! Randevu almak, randevunuzu değiştirmek ya da sorularınız için bana yazabilirsiniz."
)
NO_BUSINESS = "Merhaba! Lütfen işletmenin randevu sayfasındaki Telegram bağlantısından başlayın."
TEXT_ONLY = "Şimdilik yalnızca yazılı mesajları anlayabiliyorum."

START = re.compile(r"^/start(?:@\w+)?(?:\s+(?P<payload>\S+))?$")
# Rezerve's own slug rule (lib/slug.ts).
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Telegram allows 4096 characters; a customer needs far fewer, and the model
# reads every one.
MAX_TEXT = 1000

type Services = Annotated[AppServices, Depends(app_services)]
type SecretHeader = Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")]

router = APIRouter(prefix="/telegram", tags=["telegram"])


class Chat(BaseModel):
    id: int
    type: str


class Message(BaseModel):
    message_id: int
    chat: Chat
    text: str | None = None


class Update(BaseModel):
    update_id: int
    message: Message | None = None


@router.post("/webhook")
async def webhook(
    update: Update, services: Services, secret: SecretHeader = None
) -> dict[str, bool]:
    telegram = services.telegram
    expected = services.settings.telegram_webhook_secret
    if telegram is None or expected is None:
        raise HTTPException(status_code=404)
    if secret is None or not hmac.compare_digest(
        secret.encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status_code=401, detail="unauthorized")
    message = update.message
    # Edits, channel posts and group chats are no conversation with a customer.
    if message is None or message.chat.type != "private":
        return {"ok": True}
    chat_id = message.chat.id
    if message.text is None:
        await telegram.send_message(chat_id, TEXT_ONLY)
        return {"ok": True}
    now = datetime.now(UTC)
    if start := START.match(message.text.strip()):
        slug = start["payload"]
        if slug is None or not SLUG.match(slug):
            await telegram.send_message(chat_id, NO_BUSINESS)
        else:
            await services.store.bind_telegram_chat(chat_id, slug, now)
            await telegram.send_message(chat_id, WELCOME)
        return {"ok": True}
    slug = await services.store.telegram_business(chat_id)
    if slug is None:
        await telegram.send_message(chat_id, NO_BUSINESS)
        return {"ok": True}
    inbound = InboundMessage(
        business_slug=slug,
        channel="telegram",
        channel_user_id=str(chat_id),
        channel_message_id=str(update.update_id),
        text=message.text[:MAX_TEXT],
    )
    secret_key = services.settings.customer_ref_secret.get_secret_value()
    recorded = await services.store.record_inbound(
        inbound, customer_ref(secret_key, "telegram", str(chat_id)), now
    )
    if recorded.seq is not None:
        await services.queue.enqueue_turn(recorded.conversation_id, recorded.seq)
    return {"ok": True}
```

In `src/assistant/services.py`:

```diff
--- a/src/assistant/services.py
+++ b/src/assistant/services.py
@@ -6,6 +6,7 @@ from typing import Any, cast
 
 from fastapi import Request
 
+from assistant.channels.telegram import TelegramApi
 from assistant.config import Settings
 from assistant.conversation.store import Store
 from assistant.queue.base import TaskQueue
@@ -25,6 +26,8 @@ class AppServices:
     # Answers a conversation's unprocessed messages: what a turn task runs.
     process_turn: Callable[[int], Awaitable[None]]
     verify_token: TokenVerifier
+    # None while no bot token is set: the Telegram webhook is off.
+    telegram: TelegramApi | None
 
 
 def app_services(request: Request) -> AppServices:
```

In `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -4,6 +4,7 @@ from collections.abc import AsyncGenerator
 from contextlib import AsyncExitStack, asynccontextmanager
 from functools import partial
 
+import httpx
 from fastapi import FastAPI
 from fastapi.middleware.cors import CORSMiddleware
 from google.cloud import tasks_v2
@@ -12,7 +13,9 @@ from pydantic_ai.models import Model
 from assistant import internal
 from assistant.agent.agent import build_agent, build_model
 from assistant.agent.approvals import ReplyClassifier
-from assistant.channels import web
+from assistant.channels import telegram_webhook, web
+from assistant.channels.base import Channel, ChannelName
+from assistant.channels.telegram import TelegramApi, TelegramChannel
 from assistant.config import Settings
 from assistant.conversation.store import Store
 from assistant.conversation.turn import OpenRezerve, TurnProcessor, TurnSettings
@@ -32,9 +35,10 @@ def create_app(
     open_rezerve: OpenRezerve | None = None,
     tasks_client: TaskCreator | None = None,
     verify_token: TokenVerifier | None = None,
+    telegram_transport: httpx.AsyncBaseTransport | None = None,
 ) -> FastAPI:
-    """Tests pass a model, a Rezerve, Cloud Tasks and a token check of their
-    own; production builds each from settings."""
+    """Tests pass a model, a Rezerve, Cloud Tasks, a token check and Telegram's
+    transport of their own; production builds each from settings."""
     config = settings or Settings()  # pyright: ignore[reportCallIssue]
 
     @asynccontextmanager
@@ -44,6 +48,14 @@ def create_app(
             stack.push_async_callback(engine.dispose)
             store = Store(session_factory(engine))
             chosen = model or build_model(config)
+            channels: dict[ChannelName, Channel] = {"web": web.WebChannel()}
+            telegram: TelegramApi | None = None
+            if config.telegram_bot_token is not None:
+                client = await stack.enter_async_context(
+                    httpx.AsyncClient(transport=telegram_transport, timeout=15)
+                )
+                telegram = TelegramApi(config.telegram_bot_token.get_secret_value(), client)
+                channels["telegram"] = TelegramChannel(telegram)
             processor = TurnProcessor(
                 store=store,
                 agent=build_agent(chosen),
@@ -52,7 +64,7 @@ def create_app(
                 or partial(
                     connect, config.rezerve_mcp_url, config.assistant_mcp_token.get_secret_value()
                 ),
-                channels={"web": web.WebChannel()},
+                channels=channels,
                 settings=TurnSettings.from_settings(config),
             )
             queue: TaskQueue
@@ -83,6 +95,7 @@ def create_app(
                 ),
                 process_turn=processor.process,
                 verify_token=verify_token or internal.verify_google_token,
+                telegram=telegram,
             )
             yield
 
@@ -96,6 +109,7 @@ def create_app(
         max_age=600,
     )
     app.include_router(web.router)
+    app.include_router(telegram_webhook.router)
     app.include_router(internal.router)
 
     @app.get("/health")
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/integration/test_telegram.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `469 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/channels/telegram_webhook.py src/assistant/db/alembic/versions/0005_telegram_chats.py \
  src/assistant/db/models.py src/assistant/conversation/store.py src/assistant/services.py \
  src/assistant/main.py tests/conftest.py tests/integration/test_telegram.py
git commit -m "feat(telegram): the webhook: a business's link binds the chat, text starts a turn

Telegram's secret header is checked in constant time. /start <slug> from the
booking page's deep link binds the chat to that business; its text is stored
once by update_id and answered. Photos get 'text only', groups are ignored.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: The owner relay, in the turn

Spec §7.6. With an owner chat linked for the business, a handoff no longer gives the customer the business's phone number.
- **The handoff:** the customer reads "Sizi işletmeye aktarıyorum, en kısa sürede dönüş yapılacak." The conversation turns `human`. The owner's Telegram chat gets the reason, the last few messages, and how to answer.
- **While the owner has it:** the customer's messages go to the owner ("Müşteri: …") without a model call or a reply.
- **After 24 hours,** or once no owner is linked, the assistant answers again and the conversation is `active` once more.

All three handoffs take this path: the model's own, the request limit, and repeated refusals.

Messages for the owner are `messages` rows with direction `owner`. So they go out through the same at-least-once sending as replies, and each sent one records which conversation it speaks for (`owner_relays`), so the owner's reply finds its way back (Task 9). Owners are linked from the command line.

**Files:**
- Create: `src/assistant/cli.py`, `src/assistant/db/alembic/versions/0006_owner_relay.py`
- Modify: `src/assistant/agent/handoff.py` (whole file below), `src/assistant/db/models.py`, `src/assistant/conversation/store.py`, `src/assistant/conversation/turn.py`, `pyproject.toml`, `tests/conftest.py`
- Test: `tests/integration/test_turn.py`, `tests/integration/test_cli.py`

**Interfaces:**
- Consumes: the `"telegram"` channel (Task 7). Owner notes are sent through `channels["telegram"]`, to the owner's chat id.
- Produces: `HANDED_OFF`, `BACK`, `HAND_BACK = "/bitir"`, `owner_notice(business_slug, reason, recent)`, `relayed(text)` and `from_owner(text)`, in `assistant.agent.handoff`.
- Produces: `TurnResult.owner_notes: list[str]` and `TurnResult.state: State | None`, with `State = Literal["active", "human"]`; and `ConversationRecord.handed_off_at: datetime | None`.
- Produces, on `Store`:
  - `link_owner(slug, chat_id)` and `unlink_owner(slug)`;
  - `owner_chat(slug) -> int | None`;
  - `recent_messages(conversation_id, limit) -> list[tuple[str, str]]`;
  - `unsent_to_owner(conversation_id) -> list[OutboundMessage]`;
  - `mark_relayed(seq, sent_message_id, owner_chat_id, conversation_id, now)`.
- Produces: `uv run assistant owners link <slug> <chat_id>` and `uv run assistant owners unlink <slug>`, through `assistant.cli:main(argv) -> int`.

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_turn.py`, the harness gains a Telegram channel, and three tests follow:

```diff
--- a/tests/integration/test_turn.py
+++ b/tests/integration/test_turn.py
@@ -21,6 +21,7 @@ from pydantic_ai.models.function import AgentInfo, FunctionModel
 from assistant.agent.agent import build_agent
 from assistant.agent.approvals import EXPIRED, ONE_AT_A_TIME, ReplyClassifier
 from assistant.agent.budget import SLOW_DOWN, Budget
+from assistant.agent.handoff import HANDED_OFF
 from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
 from assistant.conversation.lease import Lease, LeaseLost
 from assistant.conversation.store import Reply, RunRecord, Store, TurnResult
@@ -57,6 +58,19 @@ class Outbox:
         return [message.text for message in self.sent]
 
 
+class TelegramOutbox:
+    """The Telegram channel, kept: who each message went to, and what it said."""
+
+    name: ChannelName = "telegram"
+
+    def __init__(self) -> None:
+        self.sent: list[tuple[str, str]] = []
+
+    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
+        self.sent.append((channel_user_id, message.text))
+        return f"telegram:{len(self.sent)}"
+
+
 class Clock:
     def __init__(self) -> None:
         self.now = datetime.now(UTC)
@@ -70,6 +84,7 @@ class Harness:
         self.store = store
         self.fake = FakeRezerve()
         self.outbox = Outbox()
+        self.telegram = TelegramOutbox()
         self.clock = Clock()
         self.open_rezerve: Callable[[str, str], RezerveSession] = lambda slug, ref: (
             self.fake.session()
@@ -94,7 +109,7 @@ class Harness:
             agent=build_agent(self.model or self.script.model),
             classifier=ReplyClassifier(self.classifier.model),
             open_rezerve=lambda slug, ref: self.open_rezerve(slug, ref),
-            channels={"web": self.outbox},
+            channels={"web": self.outbox, "telegram": self.telegram},
             settings=self.settings,
             clock=self.clock,
         )
@@ -600,3 +615,49 @@ async def test_a_sender_past_the_limit_is_asked_to_slow_down_once_then_not_answe
     assert await turn.say("??") == [SLOW_DOWN]
     assert await turn.say("cevap verin") == []
     assert await store.unprocessed_inbound(turn.conversation_id) == []
+
+
+OWNER_CHAT = 777
+
+
+async def test_a_handoff_puts_a_linked_owner_in_the_conversation(store: Store) -> None:
+    # Spec §7.6: the customer is told, and the owner's Telegram chat gets the
+    # reason, the last messages, and how to answer.
+    await store.link_owner("demo", OWNER_CHAT)
+    turn = Harness(store, Script(calls("handoff_to_owner", {"reason": "şikayet"})))
+
+    assert await turn.say("berbat bir hizmetti, şikayetçiyim") == [HANDED_OFF]
+
+    [(chat, notice)] = turn.telegram.sent
+    assert chat == str(OWNER_CHAT)
+    assert "Neden: şikayet" in notice
+    assert "Müşteri: berbat bir hizmetti, şikayetçiyim" in notice
+    assert "/bitir" in notice
+    assert (await store.conversation(turn.conversation_id)).state == "human"
+
+
+async def test_while_the_owner_has_it_the_customer_is_relayed_and_no_model_asked(
+    store: Store,
+) -> None:
+    await store.link_owner("demo", OWNER_CHAT)
+    # One step only: a second model request would fail the test.
+    turn = Harness(store, Script(calls("handoff_to_owner", {"reason": "şikayet"})))
+    await turn.say("şikayetçiyim")
+
+    assert await turn.say("ne zaman dönüş yapacaksınız?") == []
+    assert turn.telegram.sent[-1] == (str(OWNER_CHAT), "Müşteri: ne zaman dönüş yapacaksınız?")
+    assert await store.unprocessed_inbound(turn.conversation_id) == []
+
+
+async def test_a_day_after_the_handoff_the_assistant_answers_again(store: Store) -> None:
+    await store.link_owner("demo", OWNER_CHAT)
+    turn = Harness(
+        store, Script(calls("handoff_to_owner", {"reason": "şikayet"}), says("Buyurun?"))
+    )
+    await turn.say("şikayetçiyim")
+    turn.clock.now += timedelta(hours=20)
+    assert await turn.say("orada mısınız?") == []
+    turn.clock.now += timedelta(hours=5)
+
+    assert await turn.say("merhaba?") == ["Buyurun?"]
+    assert (await store.conversation(turn.conversation_id)).state == "active"
```

Create `tests/integration/test_cli.py`:

```python
import asyncio

import pytest

from assistant.cli import main
from assistant.conversation.store import Store
from assistant.db.session import create_engine, session_factory

SLUG = "cli-test-salon"


def owner_of(database_url: str, slug: str) -> int | None:
    async def read() -> int | None:
        engine = create_engine(database_url)
        try:
            return await Store(session_factory(engine)).owner_chat(slug)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_an_owner_chat_is_linked_from_the_command_line(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Spec §7.6: `uv run assistant owners link <slug> <telegram_chat_id>`.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("CONFIG_FILE", raising=False)

    assert main(["owners", "link", SLUG, "777"]) == 0
    assert owner_of(database_url, SLUG) == 777
    assert main(["owners", "link", SLUG, "888"]) == 0
    assert owner_of(database_url, SLUG) == 888
    assert main(["owners", "unlink", SLUG]) == 0
    assert owner_of(database_url, SLUG) is None
    assert SLUG in capsys.readouterr().out
```

In `tests/conftest.py`:

```diff
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -33,7 +33,7 @@ async def store(database_url: str) -> AsyncIterator[Store]:
         await connection.execute(
             sa.text(
                 "TRUNCATE conversations, messages, pending_approvals, agent_runs, "
-                "telegram_chats RESTART IDENTITY CASCADE"
+                "telegram_chats, business_config, owner_relays RESTART IDENTITY CASCADE"
             )
         )
     yield Store(session_factory(engine))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/integration/test_turn.py tests/integration/test_cli.py -q`
Expected: collection fails:
- `ImportError: cannot import name 'HANDED_OFF' from 'assistant.agent.handoff'`;
- `ModuleNotFoundError: No module named 'assistant.cli'`.

- [ ] **Step 3: Write the relay**

Create `src/assistant/db/alembic/versions/0006_owner_relay.py`:

```python
"""The owner relay: who owns each business's handoffs, and which owner
message speaks for which conversation.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "business_config",
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("owner_telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("slug"),
    )
    op.create_table(
        "owner_relays",
        sa.Column("owner_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_chat_id", "telegram_message_id"),
    )


def downgrade() -> None:
    op.drop_table("owner_relays")
    op.drop_table("business_config")
    op.drop_column("conversations", "handed_off_at")
```

In `src/assistant/db/models.py`, `handed_off_at` on `Conversation`, and two tables:

```diff
--- a/src/assistant/db/models.py
+++ b/src/assistant/db/models.py
@@ -1,5 +1,4 @@
-"""The agent's own tables (spec §7.8). Phase 2 needs these four; the owner
-relay, reminders and the daily usage counter arrive with their phases.
+"""The agent's own tables (spec §7.8).
 
 `sa.text` is spelled out because `messages.text` is a column: inside a
 class body a bare `text(...)` would call the column, not SQLAlchemy.
@@ -32,6 +31,8 @@ class Conversation(Base):
     # The lease: who is working on this conversation, and until when.
     locked_until: Mapped[datetime | None]
     lease_owner: Mapped[str | None] = mapped_column(sa.Text)
+    # When the business owner took the conversation over (state 'human').
+    handed_off_at: Mapped[datetime | None]
     last_message_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
     created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
 
@@ -166,3 +167,28 @@ class TelegramChat(Base):
     chat_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
     business_slug: Mapped[str] = mapped_column(sa.Text)
     updated_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
+
+
+class BusinessConfig(Base):
+    """What this service knows of a business beyond Rezerve: its owner's chat."""
+
+    __tablename__ = "business_config"
+
+    slug: Mapped[str] = mapped_column(sa.Text, primary_key=True)
+    # The Telegram chat a handoff goes to; none, and the customer gets the phone.
+    owner_telegram_chat_id: Mapped[int | None] = mapped_column(sa.BigInteger)
+    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
+
+
+class OwnerRelay(Base):
+    """A message in an owner's chat, and the conversation it speaks for: the
+    owner answers a customer by replying to it."""
+
+    __tablename__ = "owner_relays"
+
+    # Telegram message ids are unique only within their chat.
+    owner_chat_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
+    telegram_message_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
+    conversation_id: Mapped[int] = mapped_column(
+        sa.ForeignKey("conversations.id", ondelete="CASCADE")
+    )
```

Replace `src/assistant/agent/handoff.py` with:

```python
"""What the customer and the owner read when the assistant steps aside.

With an owner chat linked, a handoff hands the conversation to the owner on
Telegram (spec §7.6). Without one, the customer gets the business's own phone
number.
"""

from collections.abc import Sequence
from urllib.parse import urljoin

from assistant.mcp import BusinessInfo

HANDED_OFF = "Sizi işletmeye aktarıyorum, en kısa sürede dönüş yapılacak."
# When the owner hands the conversation back, or their 24 hours are over.
BACK = "Asistan yeniden sizinle. Size nasıl yardımcı olabilirim?"
HAND_BACK = "/bitir"


def handoff_reply(business: BusinessInfo) -> str:
    if business.phone:
        return (
            "Bu konuda size en doğru yanıtı işletme verebilir: "
            f"{business.phone} numarasından ulaşabilirsiniz."
        )
    return (
        "Bu konuda size en doğru yanıtı işletme verebilir. "
        f"İletişim bilgileri randevu sayfasında: {business.booking_page_url}"
    )


def owner_notice(business_slug: str, reason: str, recent: Sequence[tuple[str, str]]) -> str:
    """The owner's first word of a handoff: why, what was said, how to answer."""
    said = [
        f"{'Müşteri' if direction == 'in' else 'Asistan'}: {text}" for direction, text in recent
    ]
    return "\n".join(
        [
            f"🔔 {business_slug}: bir müşteri size aktarıldı.",
            f"Neden: {reason}",
            "",
            "Son mesajlar:",
            *said,
            "",
            "Yanıtlamak için bu mesajı yanıtlayın. "
            f"Sohbeti asistana geri vermek için {HAND_BACK} yazarak yanıtlayın.",
        ]
    )


def relayed(text: str) -> str:
    """What the customer wrote, as the owner reads it."""
    return f"Müşteri: {text}"


def from_owner(text: str) -> str:
    """What the owner wrote, as the customer reads it."""
    return f"İşletme: {text}"


def booking_page(rezerve_mcp_url: str, business_slug: str) -> str:
    """The booking page, worked out from the MCP address when Rezerve is unreachable."""
    return urljoin(rezerve_mcp_url, f"/r/{business_slug}")


def unavailable_reply(booking_page_url: str) -> str:
    """Spec §7.5: the customer always gets an answer."""
    return f"Şu an yanıt veremiyorum — randevunuzu buradan alabilirsiniz: {booking_page_url}"
```

In `src/assistant/conversation/store.py`, the state change and the owner's notes in `commit_turn`, and the owner's methods:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -13,7 +13,15 @@ from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
 
 from assistant.channels.base import Action, ChannelName, InboundMessage, OutboundMessage
 from assistant.conversation import history
-from assistant.db.models import AgentRun, Conversation, Message, PendingApproval, TelegramChat
+from assistant.db.models import (
+    AgentRun,
+    BusinessConfig,
+    Conversation,
+    Message,
+    OwnerRelay,
+    PendingApproval,
+    TelegramChat,
+)
 
 # A conversation idle this long is closed; the next message opens a new one.
 IDLE_CLOSE = timedelta(hours=24)
@@ -23,6 +31,7 @@ PAGE_SIZE = 50
 
 type Decision = Literal["approved", "denied", "expired"]
 type Outcome = Literal["reply", "confirmation", "handoff", "degraded", "error"]
+type State = Literal["active", "human"]
 
 
 @dataclass(frozen=True)
@@ -41,6 +50,8 @@ class ConversationRecord:
     customer_ref: str
     state: str
     history: list[ModelMessage]
+    # When the owner took it over; None unless the state is 'human'.
+    handed_off_at: datetime | None = None
 
 
 @dataclass(frozen=True)
@@ -104,6 +115,10 @@ class TurnResult:
     run: RunRecord | None = None
     resolved: tuple[int, Decision] | None = None
     approval: NewApproval | None = None
+    # Messages for the business owner's Telegram chat (spec §7.6).
+    owner_notes: list[str] = field(default_factory=list[str])
+    # The conversation's new state: 'human' hands it to the owner, 'active' back.
+    state: State | None = None
 
 
 @dataclass(frozen=True)
@@ -216,6 +231,7 @@ class Store:
                 customer_ref=row.customer_ref,
                 state=row.state,
                 history=history.load(row.history),
+                handed_off_at=row.handed_off_at,
             )
 
     async def claim_lease(self, conversation_id: int, owner: str, ttl: timedelta) -> bool:
@@ -309,6 +325,15 @@ class Store:
                     .where(Conversation.id == conversation_id)
                     .values(history=history.dump(result.history))
                 )
+            if result.state is not None:
+                await session.execute(
+                    sa.update(Conversation)
+                    .where(Conversation.id == conversation_id)
+                    .values(
+                        state=result.state,
+                        handed_off_at=now if result.state == "human" else None,
+                    )
+                )
             channel = (
                 await session.execute(
                     sa.select(Conversation.channel).where(Conversation.id == conversation_id)
@@ -325,6 +350,15 @@ class Store:
                 for reply in result.replies
             ]
             session.add_all(replies)
+            session.add_all(
+                Message(
+                    conversation_id=conversation_id,
+                    channel="telegram",
+                    direction="owner",
+                    text=note,
+                )
+                for note in result.owner_notes
+            )
             await session.flush()
             if result.approval is not None:
                 approval = result.approval
@@ -462,6 +496,85 @@ class Store:
                 sa.select(TelegramChat.business_slug).where(TelegramChat.chat_id == chat_id)
             )
 
+    async def link_owner(self, business_slug: str, owner_chat_id: int) -> None:
+        """Handoffs for this business go to this Telegram chat from now on."""
+        async with self._sessions.begin() as session:
+            await session.execute(
+                pg_insert(BusinessConfig)
+                .values(slug=business_slug, owner_telegram_chat_id=owner_chat_id)
+                .on_conflict_do_update(
+                    index_elements=["slug"], set_={"owner_telegram_chat_id": owner_chat_id}
+                )
+            )
+
+    async def unlink_owner(self, business_slug: str) -> None:
+        async with self._sessions.begin() as session:
+            await session.execute(
+                sa.update(BusinessConfig)
+                .where(BusinessConfig.slug == business_slug)
+                .values(owner_telegram_chat_id=None)
+            )
+
+    async def owner_chat(self, business_slug: str) -> int | None:
+        async with self._sessions() as session:
+            return await session.scalar(
+                sa.select(BusinessConfig.owner_telegram_chat_id).where(
+                    BusinessConfig.slug == business_slug
+                )
+            )
+
+    async def recent_messages(self, conversation_id: int, limit: int) -> list[tuple[str, str]]:
+        """The last `limit` messages between customer and assistant, oldest first."""
+        async with self._sessions() as session:
+            rows = await session.execute(
+                sa.select(Message.direction, Message.text)
+                .where(Message.conversation_id == conversation_id, Message.direction != "owner")
+                .order_by(Message.seq.desc())
+                .limit(limit)
+            )
+            return [(row.direction, row.text) for row in reversed(rows.all())]
+
+    async def unsent_to_owner(self, conversation_id: int) -> list[OutboundMessage]:
+        async with self._sessions() as session:
+            rows = await session.execute(
+                sa.select(Message.seq, Message.text)
+                .where(
+                    Message.conversation_id == conversation_id,
+                    Message.direction == "owner",
+                    Message.sent_at.is_(None),
+                )
+                .order_by(Message.seq)
+            )
+            return [OutboundMessage(seq=row.seq, text=row.text) for row in rows]
+
+    async def mark_relayed(
+        self,
+        seq: int,
+        sent_message_id: str,
+        owner_chat_id: int,
+        conversation_id: int,
+        now: datetime,
+    ) -> None:
+        """Mark an owner's message sent, and remember which conversation it
+        speaks for, so the owner's reply to it finds its way back."""
+        _, _, message_id = sent_message_id.partition(":")
+        async with self._sessions.begin() as session:
+            await session.execute(
+                sa.update(Message)
+                .where(Message.seq == seq)
+                .values(sent_at=now, sent_message_id=sent_message_id)
+            )
+            if message_id.isdigit():
+                await session.execute(
+                    pg_insert(OwnerRelay)
+                    .values(
+                        owner_chat_id=owner_chat_id,
+                        telegram_message_id=int(message_id),
+                        conversation_id=conversation_id,
+                    )
+                    .on_conflict_do_nothing()
+                )
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

In `src/assistant/conversation/turn.py`, one path for every handoff, the relay while the owner has it, and sending to the owner:

```diff
--- a/src/assistant/conversation/turn.py
+++ b/src/assistant/conversation/turn.py
@@ -8,6 +8,7 @@ are sent after it and marked sent one by one, so a retry resends only what
 never went out (at-least-once, spec §7.2).
 """
 
+import dataclasses
 import logging
 import time
 from collections.abc import Callable, Mapping, Sequence
@@ -46,7 +47,14 @@ from assistant.agent.approvals import (
     write_receipt,
 )
 from assistant.agent.budget import SLOW_DOWN, Budget
-from assistant.agent.handoff import booking_page, handoff_reply, unavailable_reply
+from assistant.agent.handoff import (
+    HANDED_OFF,
+    booking_page,
+    handoff_reply,
+    owner_notice,
+    relayed,
+    unavailable_reply,
+)
 from assistant.channels.base import Channel, ChannelName
 from assistant.config import Settings
 from assistant.conversation import history
@@ -59,6 +67,7 @@ from assistant.conversation.store import (
     Outcome,
     Reply,
     RunRecord,
+    State,
     Store,
     StoredInbound,
     TurnResult,
@@ -70,6 +79,10 @@ log = logging.getLogger(__name__)
 # Calls code may send back to the model in one turn — a service id it made up,
 # an email it forgot to ask for — before the business is handed the customer.
 MAX_REFUSALS = 2
+# How long an owner keeps a conversation they were handed (spec §7.6).
+OWNER_KEEPS = timedelta(hours=24)
+# Messages the owner sees with a handoff.
+RECENT = 6
 
 type OpenRezerve = Callable[[str, str], RezerveSession]
 
@@ -178,6 +191,8 @@ class _Turn:
         *,
         messages: list[ModelMessage] | None = None,
         approval: NewApproval | None = None,
+        owner_notes: list[str] | None = None,
+        state: State | None = None,
     ) -> TurnResult:
         receipt = write_receipt(self.writes)
         if receipt is not None and outcome != "reply":
@@ -209,6 +224,8 @@ class _Turn:
             run=run,
             resolved=self.resolved,
             approval=approval,
+            owner_notes=owner_notes or [],
+            state=state,
         )
 
 
@@ -253,7 +270,13 @@ class TurnProcessor:
             conversation = await self._store.conversation(conversation_id)
             await self._send_unsent(conversation)
             while batch := await self._store.unprocessed_inbound(conversation_id):
-                result = await self._answer(conversation, batch, lease.check)
+                if await self._owner_has(conversation):
+                    result = self._relay(batch)
+                else:
+                    result = await self._answer(conversation, batch, lease.check)
+                    if conversation.state == "human" and result.state is None:
+                        # The owner's 24 hours are over, or no owner is linked now.
+                        result = dataclasses.replace(result, state="active")
                 lease.check()
                 await self._store.commit_turn(conversation_id, result, self._clock())
                 await self._send_unsent(conversation)
@@ -264,6 +287,57 @@ class TurnProcessor:
         for message in await self._store.unsent(conversation.id):
             sent_id = await channel.send(conversation.channel_user_id, message)
             await self._store.mark_sent(message.seq, sent_id, self._clock())
+        notes = await self._store.unsent_to_owner(conversation.id)
+        if not notes:
+            return
+        owner = await self._owner_chat(conversation.business_slug)
+        telegram = self._channels.get("telegram")
+        for note in notes:
+            if owner is None or telegram is None:
+                # Unlinked since it was written: there is no one to tell.
+                await self._store.mark_sent(note.seq, "owner:unlinked", self._clock())
+                continue
+            sent_id = await telegram.send(str(owner), note)
+            await self._store.mark_relayed(note.seq, sent_id, owner, conversation.id, self._clock())
+
+    async def _owner_chat(self, business_slug: str) -> int | None:
+        """The business owner's Telegram chat, if one is linked and Telegram is on."""
+        if "telegram" not in self._channels:
+            return None
+        return await self._store.owner_chat(business_slug)
+
+    async def _owner_has(self, conversation: ConversationRecord) -> bool:
+        """Whether the owner is answering this conversation now (spec §7.6)."""
+        handed_off_at = conversation.handed_off_at
+        if conversation.state != "human" or handed_off_at is None:
+            return False
+        if handed_off_at <= self._clock() - OWNER_KEEPS:
+            return False
+        return await self._owner_chat(conversation.business_slug) is not None
+
+    def _relay(self, batch: list[StoredInbound]) -> TurnResult:
+        """The customer's words, passed to the owner: no model, no reply."""
+        text = "\n".join(message.text for message in batch)
+        return TurnResult(
+            processed=[message.seq for message in batch], replies=[], owner_notes=[relayed(text)]
+        )
+
+    async def _hand_off(
+        self,
+        conversation: ConversationRecord,
+        turn: "_Turn",
+        business: BusinessInfo,
+        reason: str,
+        messages: list[ModelMessage] | None = None,
+    ) -> TurnResult:
+        """To the owner's chat when one is linked; otherwise the business's phone."""
+        if await self._owner_chat(conversation.business_slug) is None:
+            return turn.finish([Reply(handoff_reply(business))], "handoff", messages=messages)
+        recent = await self._store.recent_messages(conversation.id, RECENT)
+        notice = owner_notice(conversation.business_slug, reason, recent)
+        return turn.finish(
+            [Reply(HANDED_OFF)], "handoff", messages=messages, owner_notes=[notice], state="human"
+        )
 
     async def _answer(
         self,
@@ -301,7 +375,9 @@ class TurnProcessor:
         except UsageLimitExceeded:
             if turn.business is None:
                 raise
-            return turn.finish([Reply(handoff_reply(turn.business))], "handoff")
+            return await self._hand_off(
+                conversation, turn, turn.business, "Asistan bu sohbette istek sınırına ulaştı."
+            )
         except (FallbackExceptionGroup, ModelAPIError):
             log.warning("Every model failed for conversation %s", conversation.id, exc_info=True)
             return turn.finish([Reply(self._unavailable(conversation, turn))], "degraded")
@@ -356,8 +432,9 @@ class TurnProcessor:
         for _ in range(MAX_REFUSALS + 1):
             output = run.output
             if isinstance(output, Handoff):
-                reply = Reply(handoff_reply(deps.business))
-                return turn.finish([reply], "handoff", messages=run.all_messages())
+                return await self._hand_off(
+                    conversation, turn, deps.business, output.reason, run.all_messages()
+                )
             if isinstance(output, str):
                 if not output.strip():
                     # An empty answer is no answer: send the booking page instead.
@@ -389,9 +466,12 @@ class TurnProcessor:
                 ),
             )
         # It kept proposing what could not be proposed: the business takes over.
-        reply = Reply(handoff_reply(deps.business))
-        return turn.finish(
-            [reply], "handoff", messages=history.close_open_calls(run.all_messages())
+        return await self._hand_off(
+            conversation,
+            turn,
+            deps.business,
+            "Asistan isteği tamamlayamadı.",
+            history.close_open_calls(run.all_messages()),
         )
 
     async def _decide(self, approval: OpenApproval, text: str, turn: _Turn) -> Resolution:
```

Create `src/assistant/cli.py`:

```python
"""Admin commands: `uv run assistant owners link <slug> <telegram_chat_id>`.

A business owner learns their chat's id by sending /id to the bot. Linking it
sends that business's handoffs to the owner (spec §7.6); a self-service
pairing in the Rezerve panel is v2.
"""

import argparse
import asyncio
from collections.abc import Sequence

from assistant.config import DatabaseSettings
from assistant.conversation.store import Store
from assistant.db.session import create_engine, session_factory


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="assistant")
    groups = parser.add_subparsers(dest="group", required=True)
    owners = groups.add_parser("owners", help="the business owners handoffs go to")
    actions = owners.add_subparsers(dest="action", required=True)
    link = actions.add_parser("link", help="send a business's handoffs to a Telegram chat")
    link.add_argument("slug")
    link.add_argument("chat_id", type=int)
    unlink = actions.add_parser("unlink", help="stop; handoffs give the business's phone again")
    unlink.add_argument("slug")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> str:
    engine = create_engine(DatabaseSettings().database_url)
    try:
        store = Store(session_factory(engine))
        if args.action == "link":
            await store.link_owner(args.slug, args.chat_id)
            return f"{args.slug}: handoffs go to Telegram chat {args.chat_id}"
        await store.unlink_owner(args.slug)
        return f"{args.slug}: no owner chat; handoffs give the business's phone"
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    print(asyncio.run(run(parse(argv))))
    return 0
```

In `pyproject.toml`, add this table right before `[dependency-groups]`, then run `uv sync` to install the command:

```toml
[project.scripts]
assistant = "assistant.cli:main"
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/integration/test_turn.py tests/integration/test_cli.py -q`
Expected: every test passes.
Run: `uv run assistant --help`
Expected: the usage line lists `{owners}`.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `473 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/cli.py src/assistant/db/alembic/versions/0006_owner_relay.py \
  src/assistant/agent/handoff.py src/assistant/db/models.py src/assistant/conversation/store.py \
  src/assistant/conversation/turn.py pyproject.toml uv.lock tests/conftest.py \
  tests/integration/test_turn.py tests/integration/test_cli.py
git commit -m "feat(relay): a handoff goes to the owner's Telegram chat

Spec §7.6: with an owner chat linked, the customer is told they are handed
over, the conversation turns human, and the owner gets the reason and the last
messages. The customer's next messages are relayed without a model; after 24
hours the assistant answers again. assistant owners link <slug> <chat_id>.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The owner's side, in Telegram

The owner answers a customer by replying, in Telegram, to the bot's notice or to a relayed message.
- **A reply** reaches the customer's channel as "İşletme: …".
- **`/bitir` as a reply** hands the conversation back to the assistant, and the customer is told so.
- **A reply to any other message** is an ordinary message.
- **`/id` from any chat** tells it its own id, which is what `assistant owners link` needs.

The demo's owner will try the bot from the chat linked as the owner's, and a test pins that case (Review Focus 1).

**Files:**
- Modify: `src/assistant/channels/telegram_webhook.py`, `src/assistant/conversation/store.py`, `tests/fakes/telegram.py`
- Test: `tests/integration/test_telegram.py`

**Interfaces:**
- Consumes: `owner_relays` and `Store.mark_relayed` (Task 8); `BACK`, `HAND_BACK`, `from_owner` (Task 8).
- Produces, on `Store`:
  - `relayed_conversation(owner_chat_id, message_id) -> int | None`;
  - `add_reply(conversation_id, text) -> int`, which returns the seq;
  - `hand_back(conversation_id, text) -> int | None`, which is None unless the conversation was `human`.
- Produces: `chat_id_reply(chat_id) -> str`, and `from_the_owner(services, telegram, conversation_id, owner_chat_id, text)`, in `telegram_webhook`.
- Produces: `FakeTelegram.delivered` and `FakeTelegram.last_id(chat_id)`.

- [ ] **Step 1: Write the failing tests**

In `tests/fakes/telegram.py`, the fake remembers the ids it gave:

```diff
--- a/tests/fakes/telegram.py
+++ b/tests/fakes/telegram.py
@@ -10,6 +10,8 @@ import httpx
 class FakeTelegram:
     def __init__(self) -> None:
         self.calls: list[tuple[str, dict[str, Any]]] = []
+        # (chat id, message id) of every message delivered, in order.
+        self.delivered: list[tuple[int, int]] = []
         self._next_id = 100
         # An error Telegram returns to every call from now on, as (status, description).
         self.fail_with: tuple[int, str] | None = None
@@ -28,6 +30,7 @@ class FakeTelegram:
                 status, json={"ok": False, "error_code": status, "description": description}
             )
         self._next_id += 1
+        self.delivered.append((int(payload.get("chat_id", 0)), self._next_id))
         return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_id}})
 
     def sent(self) -> list[dict[str, Any]]:
@@ -36,3 +39,7 @@ class FakeTelegram:
 
     def texts(self, chat_id: int) -> list[str]:
         return [message["text"] for message in self.sent() if message["chat_id"] == chat_id]
+
+    def last_id(self, chat_id: int) -> int:
+        """The message id Telegram gave the last message sent to this chat."""
+        return [message_id for chat, message_id in self.delivered if chat == chat_id][-1]
```

In `tests/integration/test_telegram.py`, the bot can write as another chat, and in reply to a message. Six tests follow:

```diff
--- a/tests/integration/test_telegram.py
+++ b/tests/integration/test_telegram.py
@@ -9,13 +9,14 @@ import httpx
 import pytest
 from fastapi import FastAPI
 
-from assistant.channels.telegram_webhook import NO_BUSINESS, TEXT_ONLY, WELCOME
+from assistant.agent.handoff import BACK, HANDED_OFF
+from assistant.channels.telegram_webhook import NO_BUSINESS, TEXT_ONLY, WELCOME, chat_id_reply
 from assistant.config import Settings
 from assistant.conversation.store import Store
 from assistant.main import create_app
 from assistant.queue.local import LocalTaskQueue
 from assistant.services import AppServices
-from tests.fakes.models import Script, says
+from tests.fakes.models import Script, calls, says
 from tests.fakes.rezerve import FakeRezerve
 from tests.fakes.telegram import FakeTelegram
 
@@ -50,17 +51,25 @@ class Bot:
         self,
         text: str,
         *,
+        chat: int = CHAT,
         chat_type: str = "private",
         secret: str | None = SECRET,
         update_id: int | None = None,
+        reply_to: int | None = None,
     ) -> httpx.Response:
-        message = {
+        message: dict[str, Any] = {
             "message_id": 1,
-            "from": {"id": CHAT, "is_bot": False, "first_name": "Elif"},
-            "chat": {"id": CHAT, "type": chat_type},
+            "from": {"id": chat, "is_bot": False, "first_name": "Elif"},
+            "chat": {"id": chat, "type": chat_type},
             "date": 1_790_000_000,
             "text": text,
         }
+        if reply_to is not None:
+            message["reply_to_message"] = {
+                "message_id": reply_to,
+                "chat": {"id": chat, "type": chat_type},
+                "date": 1_790_000_000,
+            }
         return await self.deliver(message, secret=secret, update_id=update_id)
 
     async def settle(self) -> None:
@@ -163,3 +172,89 @@ async def test_only_telegram_knows_the_webhook_secret(bot: Bot, secret: str | No
 @pytest.mark.parametrize("bot_overrides", [{"telegram_bot_token": None}])
 async def test_without_a_bot_there_is_no_webhook(bot: Bot) -> None:
     assert (await bot.write("/start demo")).status_code == 404
+
+
+OWNER = 9090
+
+
+async def handed_to_the_owner(bot: Bot, store: Store) -> int:
+    """A customer handed off to the linked owner; returns the notice's message id."""
+    await store.link_owner("demo", OWNER)
+    bot.script.add(calls("handoff_to_owner", {"reason": "şikayet"}))
+    await bot.write("/start demo")
+    await bot.write("berbat bir hizmetti, şikayetçiyim")
+    await bot.settle()
+    assert bot.telegram.texts(CHAT)[-1] == HANDED_OFF
+    return bot.telegram.last_id(OWNER)
+
+
+async def test_the_owner_answers_the_customer_by_replying_to_the_notice(
+    bot: Bot, store: Store
+) -> None:
+    notice = await handed_to_the_owner(bot, store)
+
+    await bot.write("Çok üzgünüz, sizi hemen arıyorum.", chat=OWNER, reply_to=notice)
+    await bot.settle()
+
+    assert bot.telegram.texts(CHAT)[-1] == "İşletme: Çok üzgünüz, sizi hemen arıyorum."
+
+
+async def test_the_customers_next_words_reach_the_owner_who_can_reply_to_them(
+    bot: Bot, store: Store
+) -> None:
+    await handed_to_the_owner(bot, store)
+    await bot.write("ne zaman arayacaksınız?")
+    await bot.settle()
+    assert bot.telegram.texts(OWNER)[-1] == "Müşteri: ne zaman arayacaksınız?"
+
+    await bot.write("10 dakika içinde.", chat=OWNER, reply_to=bot.telegram.last_id(OWNER))
+    await bot.settle()
+    assert bot.telegram.texts(CHAT)[-1] == "İşletme: 10 dakika içinde."
+
+
+async def test_bitir_hands_the_conversation_back_to_the_assistant(bot: Bot, store: Store) -> None:
+    notice = await handed_to_the_owner(bot, store)
+
+    await bot.write("/bitir", chat=OWNER, reply_to=notice)
+    await bot.settle()
+
+    assert bot.telegram.texts(CHAT)[-1] == BACK
+    bot.script.add(says("Buyurun, nasıl yardımcı olabilirim?"))
+    await bot.write("yarın yer var mı?")
+    await bot.settle()
+    assert bot.telegram.texts(CHAT)[-1] == "Buyurun, nasıl yardımcı olabilirim?"
+
+
+async def test_a_reply_to_any_other_message_is_an_ordinary_message(bot: Bot) -> None:
+    bot.script.add(says("Merhaba!"))
+    await bot.write("/start demo")
+    await bot.write("merhaba", reply_to=bot.telegram.last_id(CHAT))
+    await bot.settle()
+    assert bot.telegram.texts(CHAT) == [WELCOME, "Merhaba!"]
+
+
+async def test_id_tells_a_chat_its_own_id(bot: Bot) -> None:
+    # How an owner finds the number `assistant owners link` needs.
+    await bot.write("/id")
+    assert bot.telegram.texts(CHAT) == [chat_id_reply(CHAT)]
+
+
+async def test_an_owner_testing_as_their_own_customer_is_told_apart_by_reply(
+    bot: Bot, store: Store
+) -> None:
+    # The demo's owner tries the bot from the chat that is linked as the
+    # owner's: what they reply to decides which of the two is speaking.
+    await store.link_owner("demo", CHAT)
+    bot.script.add(calls("handoff_to_owner", {"reason": "şikayet"}))
+    await bot.write("/start demo")
+    await bot.write("şikayetçiyim")
+    await bot.settle()
+    notice = bot.telegram.last_id(CHAT)
+
+    await bot.write("orada mısınız?")
+    await bot.settle()
+    assert bot.telegram.texts(CHAT)[-1] == "Müşteri: orada mısınız?"
+
+    await bot.write("Buradayım.", reply_to=notice)
+    await bot.settle()
+    assert bot.telegram.texts(CHAT)[-1] == "İşletme: Buradayım."
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/integration/test_telegram.py -q`
Expected: collection fails: `ImportError: cannot import name 'chat_id_reply' from 'assistant.channels.telegram_webhook'`.

- [ ] **Step 3: Route the owner's replies**

In `src/assistant/conversation/store.py`, before `decisions`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -575,6 +575,49 @@ class Store:
                     .on_conflict_do_nothing()
                 )
 
+    async def relayed_conversation(self, owner_chat_id: int, message_id: int) -> int | None:
+        """The conversation an owner's message speaks for, if it is one of ours."""
+        async with self._sessions() as session:
+            return await session.scalar(
+                sa.select(OwnerRelay.conversation_id).where(
+                    OwnerRelay.owner_chat_id == owner_chat_id,
+                    OwnerRelay.telegram_message_id == message_id,
+                )
+            )
+
+    async def add_reply(self, conversation_id: int, text: str) -> int:
+        """A reply written outside a turn (the owner's, say), waiting to be sent."""
+        async with self._sessions.begin() as session:
+            channel = await session.scalar(
+                sa.select(Conversation.channel).where(Conversation.id == conversation_id)
+            )
+            message = Message(
+                conversation_id=conversation_id, channel=channel, direction="out", text=text
+            )
+            session.add(message)
+            await session.flush()
+            return message.seq
+
+    async def hand_back(self, conversation_id: int, text: str) -> int | None:
+        """The owner is done: the assistant answers again, and the customer is
+        told so. None if the conversation was not the owner's."""
+        async with self._sessions.begin() as session:
+            handed = await session.execute(
+                sa.update(Conversation)
+                .where(Conversation.id == conversation_id, Conversation.state == "human")
+                .values(state="active", handed_off_at=None)
+                .returning(Conversation.channel)
+            )
+            channel = handed.scalar_one_or_none()
+            if channel is None:
+                return None
+            message = Message(
+                conversation_id=conversation_id, channel=channel, direction="out", text=text
+            )
+            session.add(message)
+            await session.flush()
+            return message.seq
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

In `src/assistant/channels/telegram_webhook.py`:

```diff
--- a/src/assistant/channels/telegram_webhook.py
+++ b/src/assistant/channels/telegram_webhook.py
@@ -13,7 +13,9 @@ from typing import Annotated
 from fastapi import APIRouter, Depends, Header, HTTPException
 from pydantic import BaseModel
 
+from assistant.agent.handoff import BACK, HAND_BACK, from_owner
 from assistant.channels.base import InboundMessage
+from assistant.channels.telegram import TelegramApi
 from assistant.identity import customer_ref
 from assistant.services import AppServices, app_services
 
@@ -23,6 +25,12 @@ WELCOME = (
 NO_BUSINESS = "Merhaba! Lütfen işletmenin randevu sayfasındaki Telegram bağlantısından başlayın."
 TEXT_ONLY = "Şimdilik yalnızca yazılı mesajları anlayabiliyorum."
 
+
+def chat_id_reply(chat_id: int) -> str:
+    """The answer to /id: what `assistant owners link` needs from an owner."""
+    return f"Bu sohbetin kimliği: {chat_id}"
+
+
 START = re.compile(r"^/start(?:@\w+)?(?:\s+(?P<payload>\S+))?$")
 # Rezerve's own slug rule (lib/slug.ts).
 SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
@@ -41,10 +49,15 @@ class Chat(BaseModel):
     type: str
 
 
+class Replied(BaseModel):
+    message_id: int
+
+
 class Message(BaseModel):
     message_id: int
     chat: Chat
     text: str | None = None
+    reply_to_message: Replied | None = None
 
 
 class Update(BaseModel):
@@ -69,6 +82,17 @@ async def webhook(
     if message is None or message.chat.type != "private":
         return {"ok": True}
     chat_id = message.chat.id
+    if message.text is not None and message.text.strip() == "/id":
+        await telegram.send_message(chat_id, chat_id_reply(chat_id))
+        return {"ok": True}
+    if message.reply_to_message is not None:
+        conversation_id = await services.store.relayed_conversation(
+            chat_id, message.reply_to_message.message_id
+        )
+        if conversation_id is not None:
+            # An owner answering the customer it was handed (spec §7.6).
+            await from_the_owner(services, telegram, conversation_id, chat_id, message.text)
+            return {"ok": True}
     if message.text is None:
         await telegram.send_message(chat_id, TEXT_ONLY)
         return {"ok": True}
@@ -99,3 +123,24 @@ async def webhook(
     if recorded.seq is not None:
         await services.queue.enqueue_turn(recorded.conversation_id, recorded.seq)
     return {"ok": True}
+
+
+async def from_the_owner(
+    services: AppServices,
+    telegram: TelegramApi,
+    conversation_id: int,
+    owner_chat_id: int,
+    text: str | None,
+) -> None:
+    """The owner's reply goes to the customer as the business's; /bitir hands
+    the conversation back to the assistant."""
+    if text is None:
+        await telegram.send_message(owner_chat_id, TEXT_ONLY)
+        return
+    if text.strip() == HAND_BACK:
+        seq = await services.store.hand_back(conversation_id, BACK)
+    else:
+        seq = await services.store.add_reply(conversation_id, from_owner(text))
+    if seq is not None:
+        # The turn sends what waits unsent, and there is nothing else to answer.
+        await services.queue.enqueue_turn(conversation_id, seq)
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/integration/test_telegram.py -q`
Expected: `15 passed`.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `479 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/channels/telegram_webhook.py src/assistant/conversation/store.py \
  tests/fakes/telegram.py tests/integration/test_telegram.py
git commit -m "feat(relay): the owner answers by replying in Telegram; /bitir hands back

A reply to the bot's notice or to a relayed message reaches the customer as
the business's; /bitir as a reply returns the conversation to the assistant.
/id tells any chat its id, for assistant owners link.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Reminders

Spec §7.7.
- **Scheduling:** after a turn commits, each booking its writes made or moved on Telegram gets a reminder task, for 24 hours before the start. That happens only when the time is at least an hour away.
- **When the task fires:** the reminder reads the booking from Rezerve. It sends nothing if the booking moved (a move schedules a reminder of its own), was cancelled, or was never paid for.
- **Dedupe:** a `reminders` row keeps a task delivered twice from sending twice.

The task names follow the turns': `reminder-<booking>-<start epoch>`.
- **On Cloud Tasks,** a reminder is a scheduled task to `/internal/reminder`, behind the same token check.
- **On the local queue,** it is a sleeping task, kept apart from turns so that a test's `drain()` never waits a day.

The processor needs the queue for reminders, and the local queue needs the processor for turns, so the app binds them late.

**Files:**
- Create: `src/assistant/reminders.py`, `src/assistant/db/alembic/versions/0007_reminders.py`, `tests/fakes/channels.py`, `tests/fakes/queue.py`
- Modify: `src/assistant/queue/base.py` (whole file below), `src/assistant/db/models.py`, `src/assistant/conversation/store.py`, `src/assistant/conversation/turn.py`, `src/assistant/mcp.py`, `src/assistant/queue/local.py`, `src/assistant/queue/cloud_tasks.py`, `src/assistant/internal.py`, `src/assistant/services.py`, `src/assistant/main.py`, `tests/conftest.py`
- Test: `tests/integration/test_reminders.py`, `tests/integration/test_turn.py`, `tests/unit/test_cloud_tasks.py`, `tests/unit/test_local_queue.py`, `tests/integration/test_internal.py`

**Interfaces:**
- Consumes: `CompletedWrite.result`'s `booking_id` and `starts_at`, from Rezerve's create and reschedule results.
- Produces:
  - `TaskQueue.enqueue_reminder(booking_id: str, starts_at: datetime, at: datetime)`, and `reminder_name(booking_id, starts_at)`;
  - `CloudTasksQueue(..., reminders_queue=...)`;
  - `LocalTaskQueue(handler, *, remind=None, ...)`.
- Produces: `OpenRezerve`, which moves to `assistant.mcp`.
- Produces, in `assistant.reminders`:
  - `REMIND_BEFORE` (24 hours) and `MIN_NOTICE` (one hour);
  - `booked(writes) -> list[tuple[str, datetime]]` and `reminder_text(booking)`;
  - `Reminders(store, open_rezerve, telegram: Channel | None, clock=...)`, with `async send(booking_id, starts_at)`.
- Produces: `TurnResult.booked`; `TurnProcessor(..., queue: TaskQueue | None = None)`; `AppServices.send_reminder`.
- Produces, on `Store`: `add_reminder(...) -> bool`, `reminder(booking_id, starts_at) -> DueReminder | None`, and `mark_reminded(...)`.
- Produces: `RecordingQueue` and `TelegramOutbox`, as test fakes.

- [ ] **Step 1: Write the failing tests**

Create `tests/fakes/channels.py`:

```python
"""Channels that keep what they were asked to send."""

from assistant.channels.base import ChannelName, OutboundMessage


class TelegramOutbox:
    """The Telegram channel, kept: who each message went to, and what it said."""

    name: ChannelName = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        self.sent.append((channel_user_id, message.text))
        return f"telegram:{len(self.sent)}"
```

Create `tests/fakes/queue.py`:

```python
"""A task queue that only keeps what it was asked: turns and reminders."""

from datetime import datetime


class RecordingQueue:
    def __init__(self) -> None:
        self.turns: list[tuple[int, int]] = []
        self.reminders: list[tuple[str, datetime, datetime]] = []

    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        self.turns.append((conversation_id, message_seq))

    async def enqueue_reminder(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
        self.reminders.append((booking_id, starts_at, at))
```

Create `tests/integration/test_reminders.py`:

```python
"""Sending a reminder when its task fires (spec §7.7): only for a booking
still confirmed at the time it was booked for, and only once."""

from datetime import UTC, datetime, timedelta

import pytest

from assistant.agent.format import local_label
from assistant.conversation.store import Store
from assistant.identity import customer_ref
from assistant.reminders import Reminders
from tests.fakes.channels import TelegramOutbox
from tests.fakes.rezerve import SAC, TIMEZONE, FakeRezerve

REF = customer_ref("secret", "telegram", "4242")
IN_THREE_DAYS = (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def fake() -> FakeRezerve:
    return FakeRezerve()


@pytest.fixture
def telegram() -> TelegramOutbox:
    return TelegramOutbox()


@pytest.fixture
def reminders(store: Store, fake: FakeRezerve, telegram: TelegramOutbox) -> Reminders:
    return Reminders(store, lambda slug, ref: fake.session(), telegram)


async def test_a_booking_still_at_its_time_is_reminded_once(
    store: Store, fake: FakeRezerve, telegram: TelegramOutbox, reminders: Reminders
) -> None:
    booking = fake.add_booking(SAC, IN_THREE_DAYS)
    assert await store.add_reminder(booking.id, IN_THREE_DAYS, "demo", REF, 4242, datetime.now(UTC))

    await reminders.send(booking.id, IN_THREE_DAYS)
    await reminders.send(booking.id, IN_THREE_DAYS)  # Cloud Tasks delivers at least once

    assert telegram.sent == [
        (
            "4242",
            "Hatırlatma: Saç Kesimi randevunuz yaklaşıyor — "
            f"{local_label(IN_THREE_DAYS, TIMEZONE)}. Görüşmek üzere!",
        )
    ]


@pytest.mark.parametrize("change", ["moved", "cancelled", "unpaid"])
async def test_a_booking_that_changed_since_is_not_reminded(
    store: Store, fake: FakeRezerve, telegram: TelegramOutbox, reminders: Reminders, change: str
) -> None:
    # A move schedules a reminder of its own; a cancelled or never-paid
    # booking has nothing to remind of.
    booking = fake.add_booking(
        SAC, IN_THREE_DAYS, status="pending" if change == "unpaid" else "confirmed"
    )
    if change == "moved":
        booking.starts_at += timedelta(hours=2)
    if change == "cancelled":
        booking.status = "cancelled"
    await store.add_reminder(booking.id, IN_THREE_DAYS, "demo", REF, 4242, datetime.now(UTC))

    await reminders.send(booking.id, IN_THREE_DAYS)

    assert telegram.sent == []


async def test_a_reminder_never_scheduled_sends_nothing(
    telegram: TelegramOutbox, reminders: Reminders
) -> None:
    await reminders.send("no-such-booking", IN_THREE_DAYS)
    assert telegram.sent == []
```

In `tests/integration/test_turn.py`, the outbox moves to the fakes, the harness can speak as a Telegram customer and keeps its queue, and four tests follow:

```diff
--- a/tests/integration/test_turn.py
+++ b/tests/integration/test_turn.py
@@ -28,7 +28,9 @@ from assistant.conversation.store import Reply, RunRecord, Store, TurnResult
 from assistant.conversation.turn import TurnProcessor, TurnSettings
 from assistant.identity import customer_ref
 from assistant.mcp import RezerveSession
+from tests.fakes.channels import TelegramOutbox
 from tests.fakes.models import Script, calls, classifies, says
+from tests.fakes.queue import RecordingQueue
 from tests.fakes.rezerve import CHECKOUT_URL, CILT, SAC, FakeRezerve
 
 REF = customer_ref("secret", "web", "visitor-1")
@@ -58,19 +60,6 @@ class Outbox:
         return [message.text for message in self.sent]
 
 
-class TelegramOutbox:
-    """The Telegram channel, kept: who each message went to, and what it said."""
-
-    name: ChannelName = "telegram"
-
-    def __init__(self) -> None:
-        self.sent: list[tuple[str, str]] = []
-
-    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
-        self.sent.append((channel_user_id, message.text))
-        return f"telegram:{len(self.sent)}"
-
-
 class Clock:
     def __init__(self) -> None:
         self.now = datetime.now(UTC)
@@ -80,8 +69,19 @@ class Clock:
 
 
 class Harness:
-    def __init__(self, store: Store, script: Script, *, classifier: Script | None = None) -> None:
+    def __init__(
+        self,
+        store: Store,
+        script: Script,
+        *,
+        classifier: Script | None = None,
+        channel: ChannelName = "web",
+        user: str = "visitor-1",
+    ) -> None:
         self.store = store
+        self.channel: ChannelName = channel
+        self.user = user
+        self.queue = RecordingQueue()
         self.fake = FakeRezerve()
         self.outbox = Outbox()
         self.telegram = TelegramOutbox()
@@ -112,20 +112,28 @@ class Harness:
             channels={"web": self.outbox, "telegram": self.telegram},
             settings=self.settings,
             clock=self.clock,
+            queue=self.queue,
         )
 
     async def receive(self, *texts: str) -> None:
+        ref = customer_ref("secret", self.channel, self.user)
         for text in texts:
-            message = InboundMessage("demo", "web", "visitor-1", str(uuid4()), text)
-            recorded = await self.store.record_inbound(message, REF, self.clock())
+            message = InboundMessage("demo", self.channel, self.user, str(uuid4()), text)
+            recorded = await self.store.record_inbound(message, ref, self.clock())
             self.conversation_id = recorded.conversation_id
 
+    def read(self) -> list[str]:
+        """Everything the customer has been sent, on their channel."""
+        if self.channel == "telegram":
+            return [text for chat, text in self.telegram.sent if chat == self.user]
+        return self.outbox.texts
+
     async def say(self, *texts: str) -> list[str]:
         """The customer writes; returns what they read back."""
-        before = len(self.outbox.sent)
+        before = len(self.read())
         await self.receive(*texts)
         await self.processor().process(self.conversation_id)
-        return self.outbox.texts[before:]
+        return self.read()[before:]
 
 
 def prompt_of(script: Script, request: int = -1) -> list[Any]:
@@ -661,3 +669,58 @@ async def test_a_day_after_the_handoff_the_assistant_answers_again(store: Store)
 
     assert await turn.say("merhaba?") == ["Buyurun?"]
     assert (await store.conversation(turn.conversation_id)).state == "active"
+
+
+IN_THREE_DAYS = (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
+
+
+def iso(instant: datetime) -> str:
+    return instant.isoformat().replace("+00:00", "Z")
+
+
+def book_haircut(starts_at: datetime) -> dict[str, Any]:
+    return {"service_id": SAC, "starts_at": iso(starts_at), "customer_name": "Elif Kaya"}
+
+
+async def test_a_booking_made_on_telegram_is_reminded_a_day_ahead(store: Store) -> None:
+    # Spec §7.7: a reminder task for 24 hours before the start.
+    script = Script(calls("create_booking", book_haircut(IN_THREE_DAYS)), says("Alındı."))
+    turn = Harness(store, script, channel="telegram", user="4242")
+    await turn.say("3 gün sonra saç kesimi, Elif Kaya")
+    await turn.say("evet")
+
+    [(booking_id, starts_at, at)] = turn.queue.reminders
+    assert booking_id in turn.fake.bookings
+    assert (starts_at, at) == (IN_THREE_DAYS, IN_THREE_DAYS - timedelta(hours=24))
+
+
+async def test_a_booking_made_on_the_web_is_not_reminded(store: Store) -> None:
+    # A web visitor cannot be reached once the page is closed.
+    script = Script(calls("create_booking", book_haircut(IN_THREE_DAYS)), says("Alındı."))
+    turn = Harness(store, script)
+    await turn.say("3 gün sonra saç kesimi, Elif Kaya")
+    await turn.say("evet")
+    assert turn.queue.reminders == []
+
+
+async def test_a_booking_too_near_for_a_day_ahead_is_not_reminded(store: Store) -> None:
+    # A day ahead would be in half an hour: the customer booked it just now.
+    soon = (datetime.now(UTC) + timedelta(hours=24, minutes=30)).replace(microsecond=0)
+    script = Script(calls("create_booking", book_haircut(soon)), says("Alındı."))
+    turn = Harness(store, script, channel="telegram", user="4242")
+    await turn.say("yarın bu saatlerde saç kesimi, Elif Kaya")
+    await turn.say("evet")
+    assert turn.queue.reminders == []
+
+
+async def test_a_move_is_reminded_at_its_new_time(store: Store) -> None:
+    later = IN_THREE_DAYS + timedelta(days=1)
+    turn = Harness(store, Script(), channel="telegram", user="4242")
+    booking = turn.fake.add_booking(SAC, IN_THREE_DAYS)
+    turn.script.add(
+        calls("reschedule_booking", {"booking_id": booking.id, "new_starts_at": iso(later)}),
+        says("Taşındı."),
+    )
+    await turn.say("randevumu bir gün sonraya alalım")
+    await turn.say("evet")
+    assert turn.queue.reminders == [(booking.id, later, later - timedelta(hours=24))]
```

In `tests/unit/test_cloud_tasks.py`:

```diff
--- a/tests/unit/test_cloud_tasks.py
+++ b/tests/unit/test_cloud_tasks.py
@@ -1,3 +1,6 @@
+import json
+from datetime import UTC, datetime
+
 from google.cloud import tasks_v2
 
 from assistant.queue.cloud_tasks import CloudTasksQueue
@@ -12,6 +15,7 @@ def tasks_queue(fake: FakeTasks) -> CloudTasksQueue:
         project="rezerve-assistant",
         region="europe-west1",
         turns_queue="turns",
+        reminders_queue="reminders",
         base_url="https://assistant.example/",
         invoker=INVOKER,
     )
@@ -38,3 +42,21 @@ async def test_asking_twice_for_one_turn_queues_it_once() -> None:
     await queue.enqueue_turn(7, 42)
     await queue.enqueue_turn(7, 42)
     assert len(fake.created) == 1
+
+
+async def test_a_reminder_is_a_task_for_its_time_on_the_reminders_queue() -> None:
+    fake = FakeTasks()
+    starts_at = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)
+    at = datetime(2026, 11, 11, 12, 30, tzinfo=UTC)
+    await tasks_queue(fake).enqueue_reminder("b-1", starts_at, at)
+
+    [(parent, task)] = fake.created
+    assert parent.endswith("/queues/reminders")
+    assert task.name.endswith(f"/tasks/reminder-b-1-{int(starts_at.timestamp())}")
+    assert task.schedule_time == at
+    assert task.http_request.url == "https://assistant.example/internal/reminder"
+    assert json.loads(task.http_request.body) == {
+        "booking_id": "b-1",
+        "starts_at": "2026-11-12T12:30:00+00:00",
+    }
+    assert task.http_request.headers["Content-Type"] == "application/json"
```

In `tests/unit/test_local_queue.py`:

```diff
--- a/tests/unit/test_local_queue.py
+++ b/tests/unit/test_local_queue.py
@@ -1,3 +1,6 @@
+import asyncio
+from datetime import UTC, datetime, timedelta
+
 import pytest
 
 from assistant.conversation.lease import LeaseBusy
@@ -44,3 +47,22 @@ async def test_a_failing_turn_is_retried_then_given_up(caplog: pytest.LogCapture
     await tasks.drain()
     assert handler.calls == [1, 1, 1]
     assert "gave up after 3 attempts" in caplog.text
+
+
+async def test_a_reminder_fires_at_its_time_and_a_drain_does_not_wait_for_it() -> None:
+    fired: list[tuple[str, datetime]] = []
+
+    async def remind(booking_id: str, starts_at: datetime) -> None:
+        fired.append((booking_id, starts_at))
+
+    tasks = LocalTaskQueue(Handler(), remind=remind)
+    starts_at = datetime.now(UTC) + timedelta(days=2)
+    await tasks.enqueue_reminder("b-1", starts_at, datetime.now(UTC) + timedelta(milliseconds=50))
+    await tasks.enqueue_reminder("b-1", starts_at, datetime.now(UTC) + timedelta(milliseconds=50))
+    await tasks.enqueue_reminder("b-2", starts_at, datetime.now(UTC) + timedelta(days=1))
+
+    await tasks.drain()  # turns only: the day-away reminder would hold it up
+    await asyncio.sleep(0.2)
+
+    assert fired == [("b-1", starts_at)]
+    await tasks.aclose()
```

In `tests/integration/test_internal.py`:

```diff
--- a/tests/integration/test_internal.py
+++ b/tests/integration/test_internal.py
@@ -124,3 +124,13 @@ async def test_a_turn_another_handler_holds_is_tried_again_later(
 
     # 429: Cloud Tasks retries it with backoff (spec §7.2).
     assert response.status_code == 429
+
+
+async def test_a_reminder_task_calls_back_with_the_invokers_token(service: Service) -> None:
+    body = {"booking_id": "no-such-booking", "starts_at": "2026-11-12T12:30:00+00:00"}
+    refused = await service.client.post("/internal/reminder", json=body)
+    assert refused.status_code == 401
+    response = await service.client.post(
+        "/internal/reminder", json=body, headers={"Authorization": "Bearer invoker-token"}
+    )
+    assert response.status_code == 204
```

In `tests/conftest.py`:

```diff
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -33,7 +33,7 @@ async def store(database_url: str) -> AsyncIterator[Store]:
         await connection.execute(
             sa.text(
                 "TRUNCATE conversations, messages, pending_approvals, agent_runs, "
-                "telegram_chats, business_config, owner_relays RESTART IDENTITY CASCADE"
+                "telegram_chats, business_config, owner_relays, reminders RESTART IDENTITY CASCADE"
             )
         )
     yield Store(session_factory(engine))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/integration/test_reminders.py -q`
Expected: collection fails: `ModuleNotFoundError: No module named 'assistant.reminders'`.

- [ ] **Step 3: Write the reminders, their table and both queues' tasks**

Create `src/assistant/db/alembic/versions/0007_reminders.py`:

```python
"""Reminders a day before a booking, and where to send them.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_slug", sa.Text(), nullable=False),
        sa.Column("customer_ref", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("booking_id", "starts_at"),
    )


def downgrade() -> None:
    op.drop_table("reminders")
```

In `src/assistant/db/models.py`:

```diff
--- a/src/assistant/db/models.py
+++ b/src/assistant/db/models.py
@@ -192,3 +192,19 @@ class OwnerRelay(Base):
     conversation_id: Mapped[int] = mapped_column(
         sa.ForeignKey("conversations.id", ondelete="CASCADE")
     )
+
+
+class Reminder(Base):
+    """A reminder a day before a booking (spec §7.7), and where to send it."""
+
+    __tablename__ = "reminders"
+
+    booking_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
+    # A move gets a reminder of its own: the old one finds the start changed.
+    starts_at: Mapped[datetime] = mapped_column(primary_key=True)
+    business_slug: Mapped[str] = mapped_column(sa.Text)
+    customer_ref: Mapped[str] = mapped_column(sa.Text)
+    chat_id: Mapped[int] = mapped_column(sa.BigInteger)
+    sent_at: Mapped[datetime | None]
+    sent_message_id: Mapped[str | None] = mapped_column(sa.Text)
+    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
```

In `src/assistant/conversation/store.py`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -20,6 +20,7 @@ from assistant.db.models import (
     Message,
     OwnerRelay,
     PendingApproval,
+    Reminder,
     TelegramChat,
 )
 
@@ -119,6 +120,16 @@ class TurnResult:
     owner_notes: list[str] = field(default_factory=list[str])
     # The conversation's new state: 'human' hands it to the owner, 'active' back.
     state: State | None = None
+    # Each booking this turn's writes made or moved, and when it now starts.
+    booked: list[tuple[str, datetime]] = field(default_factory=list[tuple[str, datetime]])
+
+
+@dataclass(frozen=True)
+class DueReminder:
+    business_slug: str
+    customer_ref: str
+    chat_id: int
+    sent_at: datetime | None
 
 
 @dataclass(frozen=True)
@@ -618,6 +629,50 @@ class Store:
             await session.flush()
             return message.seq
 
+    async def add_reminder(
+        self,
+        booking_id: str,
+        starts_at: datetime,
+        business_slug: str,
+        customer_ref: str,
+        chat_id: int,
+        now: datetime,
+    ) -> bool:
+        """Keep a reminder to send; False if this one was already kept."""
+        async with self._sessions.begin() as session:
+            # KVKK: a reminder long past holds a chat id and nothing worth keeping.
+            await session.execute(sa.delete(Reminder).where(Reminder.starts_at < now - RETENTION))
+            added = await session.execute(
+                pg_insert(Reminder)
+                .values(
+                    booking_id=booking_id,
+                    starts_at=starts_at,
+                    business_slug=business_slug,
+                    customer_ref=customer_ref,
+                    chat_id=chat_id,
+                )
+                .on_conflict_do_nothing()
+                .returning(Reminder.booking_id)
+            )
+            return added.scalar_one_or_none() is not None
+
+    async def reminder(self, booking_id: str, starts_at: datetime) -> DueReminder | None:
+        async with self._sessions() as session:
+            row = await session.get(Reminder, (booking_id, starts_at))
+            if row is None:
+                return None
+            return DueReminder(row.business_slug, row.customer_ref, row.chat_id, row.sent_at)
+
+    async def mark_reminded(
+        self, booking_id: str, starts_at: datetime, sent_message_id: str, now: datetime
+    ) -> None:
+        async with self._sessions.begin() as session:
+            await session.execute(
+                sa.update(Reminder)
+                .where(Reminder.booking_id == booking_id, Reminder.starts_at == starts_at)
+                .values(sent_at=now, sent_message_id=sent_message_id)
+            )
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

In `src/assistant/mcp.py`, `OpenRezerve` moves here from `turn.py`:

```diff
--- a/src/assistant/mcp.py
+++ b/src/assistant/mcp.py
@@ -8,6 +8,7 @@ same session to build the instructions and the confirmations.
 
 import dataclasses
 import json
+from collections.abc import Callable
 from dataclasses import dataclass, field
 from datetime import datetime, timedelta
 from types import TracebackType
@@ -250,6 +251,10 @@ class RezerveSession:
         )
 
 
+# Opens a session for (business slug, customer ref).
+type OpenRezerve = Callable[[str, str], RezerveSession]
+
+
 def connect(url: str, token: str, business_slug: str, customer_ref: str) -> RezerveSession:
     headers = {
         "Authorization": f"Bearer {token}",
```

Create `src/assistant/reminders.py`:

```python
"""Reminders (spec §7.7): a day before a booking the agent made or moved, on
Telegram. WhatsApp reminders need paid templates (v2), and a web visitor
cannot be reached once the page is closed.

A move schedules a reminder of its own, and the old one finds, when it fires,
that the booking no longer starts when it said: it sends nothing. Nor does
one for a booking cancelled since, or never paid for.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from assistant.channels.base import Channel, OutboundMessage
from assistant.conversation.store import Store
from assistant.mcp import BookingView, CompletedWrite, OpenRezerve, RezerveError

log = logging.getLogger(__name__)

REMIND_BEFORE = timedelta(hours=24)
# A reminder due sooner than this is no reminder: the booking was just made.
MIN_NOTICE = timedelta(hours=1)
MOVES = frozenset({"create_booking", "reschedule_booking"})


def booked(writes: Sequence[CompletedWrite]) -> list[tuple[str, datetime]]:
    """Each booking these writes made or moved, and when it now starts."""
    found: list[tuple[str, datetime]] = []
    for write in writes:
        result = write.result or {}
        if write.tool_name in MOVES and "booking_id" in result and "starts_at" in result:
            starts_at = datetime.fromisoformat(str(result["starts_at"]))
            found.append((str(result["booking_id"]), starts_at))
    return found


def reminder_text(booking: BookingView) -> str:
    return (
        f"Hatırlatma: {booking.service_name} randevunuz yaklaşıyor — "
        f"{booking.local_label}. Görüşmek üzere!"
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Reminders:
    """Sends a reminder when its task fires."""

    def __init__(
        self,
        store: Store,
        open_rezerve: OpenRezerve,
        telegram: Channel | None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._open_rezerve = open_rezerve
        self._telegram = telegram
        self._clock = clock

    async def send(self, booking_id: str, starts_at: datetime) -> None:
        due = await self._store.reminder(booking_id, starts_at)
        if due is None or due.sent_at is not None or self._telegram is None:
            return
        try:
            async with self._open_rezerve(due.business_slug, due.customer_ref) as rezerve:
                booking = await rezerve.booking(booking_id)
        except RezerveError as error:
            log.info("No reminder for booking %s: %s", booking_id, error.code)
            return
        if booking.status != "confirmed" or booking.starts_at != starts_at:
            return
        message = OutboundMessage(seq=0, text=reminder_text(booking))
        sent = await self._telegram.send(str(due.chat_id), message)
        await self._store.mark_reminded(booking_id, starts_at, sent, self._clock())
```

Replace `src/assistant/queue/base.py` with:

```python
from datetime import datetime
from typing import Protocol


class TaskQueue(Protocol):
    async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
        """Ask for the conversation's unprocessed messages to be answered.

        Named after the message that asked, so asking twice is asking once.
        """
        ...

    async def enqueue_reminder(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
        """Ask for the reminder of a booking starting at `starts_at` to be sent at `at`.

        Named after the booking and its start: a move is a new reminder.
        """
        ...


def reminder_name(booking_id: str, starts_at: datetime) -> str:
    return f"reminder-{booking_id}-{int(starts_at.timestamp())}"
```

In `src/assistant/queue/local.py`:

```diff
--- a/src/assistant/queue/local.py
+++ b/src/assistant/queue/local.py
@@ -5,8 +5,10 @@ import asyncio
 import logging
 from collections import deque
 from collections.abc import Awaitable, Callable
+from datetime import UTC, datetime
 
 from assistant.conversation.lease import LeaseBusy
+from assistant.queue.base import reminder_name
 
 log = logging.getLogger(__name__)
 
@@ -20,16 +22,20 @@ class LocalTaskQueue:
         self,
         handler: Callable[[int], Awaitable[None]],
         *,
+        remind: Callable[[str, datetime], Awaitable[None]] | None = None,
         max_attempts: int = 6,
         backoff_seconds: float = 2.0,
         max_backoff_seconds: float = 60.0,
     ) -> None:
         self._handler = handler
+        self._remind = remind
         self._max_attempts = max_attempts
         self._backoff = backoff_seconds
         self._max_backoff = max_backoff_seconds
         self._names: deque[str] = deque(maxlen=REMEMBERED_NAMES)
         self._tasks: set[asyncio.Task[None]] = set()
+        # Kept apart: a drain waits for turns, never for a reminder a day away.
+        self._reminders: set[asyncio.Task[None]] = set()
 
     async def enqueue_turn(self, conversation_id: int, message_seq: int) -> None:
         name = f"turn-{conversation_id}-{message_seq}"
@@ -40,6 +46,23 @@ class LocalTaskQueue:
         self._tasks.add(task)
         task.add_done_callback(self._tasks.discard)
 
+    async def enqueue_reminder(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
+        name = reminder_name(booking_id, starts_at)
+        if self._remind is None or name in self._names:
+            return
+        self._names.append(name)
+        task = asyncio.create_task(self._remind_at(booking_id, starts_at, at), name=name)
+        self._reminders.add(task)
+        task.add_done_callback(self._reminders.discard)
+
+    async def _remind_at(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
+        assert self._remind is not None
+        await asyncio.sleep(max(0.0, (at - datetime.now(UTC)).total_seconds()))
+        try:
+            await self._remind(booking_id, starts_at)
+        except Exception:
+            log.exception("The reminder for booking %s failed", booking_id)
+
     async def _run(self, conversation_id: int) -> None:
         for attempt in range(1, self._max_attempts + 1):
             try:
@@ -58,6 +81,7 @@ class LocalTaskQueue:
             await asyncio.gather(*self._tasks, return_exceptions=True)
 
     async def aclose(self) -> None:
-        for task in self._tasks:
+        pending = [*self._tasks, *self._reminders]
+        for task in pending:
             task.cancel()
-        await asyncio.gather(*self._tasks, return_exceptions=True)
+        await asyncio.gather(*pending, return_exceptions=True)
```

In `src/assistant/queue/cloud_tasks.py`:

```diff
--- a/src/assistant/queue/cloud_tasks.py
+++ b/src/assistant/queue/cloud_tasks.py
@@ -6,12 +6,16 @@ Cloud Tasks refuses a task name it has seen in about the last hour, so a
 redelivered webhook asks for its turn once, as it does on the local queue.
 """
 
+import json
 import logging
+from datetime import datetime
 from typing import Protocol
 
 from google.api_core.exceptions import AlreadyExists
 from google.cloud import tasks_v2
 
+from assistant.queue.base import reminder_name
+
 log = logging.getLogger(__name__)
 
 Client = tasks_v2.CloudTasksAsyncClient
@@ -31,6 +35,7 @@ class CloudTasksQueue:
         project: str,
         region: str,
         turns_queue: str,
+        reminders_queue: str,
         base_url: str,
         invoker: str,
     ) -> None:
@@ -38,6 +43,7 @@ class CloudTasksQueue:
         self._project = project
         self._region = region
         self._turns = turns_queue
+        self._reminders = reminders_queue
         self._base_url = base_url.rstrip("/")
         self._invoker = invoker
 
@@ -48,17 +54,32 @@ class CloudTasksQueue:
             f"/internal/turn/{conversation_id}",
         )
 
-    async def _create(self, queue: str, name: str, path: str) -> None:
+    async def enqueue_reminder(self, booking_id: str, starts_at: datetime, at: datetime) -> None:
+        body = {"booking_id": booking_id, "starts_at": starts_at.isoformat()}
+        await self._create(
+            self._reminders,
+            reminder_name(booking_id, starts_at),
+            "/internal/reminder",
+            body=json.dumps(body).encode(),
+            at=at,
+        )
+
+    async def _create(
+        self, queue: str, name: str, path: str, *, body: bytes = b"", at: datetime | None = None
+    ) -> None:
         parent = Client.queue_path(self._project, self._region, queue)
         task = tasks_v2.Task(
             name=Client.task_path(self._project, self._region, queue, name),
             http_request=tasks_v2.HttpRequest(
                 url=f"{self._base_url}{path}",
                 http_method=tasks_v2.HttpMethod.POST,
+                headers={"Content-Type": "application/json"} if body else {},
+                body=body,
                 oidc_token=tasks_v2.OidcToken(
                     service_account_email=self._invoker, audience=self._base_url
                 ),
             ),
+            schedule_time=at,
         )
         try:
             await self._client.create_task(parent=parent, task=task)
```

In `src/assistant/conversation/turn.py`, the reminders scheduled after each commit:

```diff
--- a/src/assistant/conversation/turn.py
+++ b/src/assistant/conversation/turn.py
@@ -72,7 +72,9 @@ from assistant.conversation.store import (
     StoredInbound,
     TurnResult,
 )
-from assistant.mcp import BusinessInfo, CompletedWrite, RezerveSession
+from assistant.mcp import BusinessInfo, CompletedWrite, OpenRezerve, RezerveSession
+from assistant.queue.base import TaskQueue
+from assistant.reminders import MIN_NOTICE, REMIND_BEFORE, booked
 
 log = logging.getLogger(__name__)
 
@@ -84,8 +86,6 @@ OWNER_KEEPS = timedelta(hours=24)
 # Messages the owner sees with a handoff.
 RECENT = 6
 
-type OpenRezerve = Callable[[str, str], RezerveSession]
-
 
 def _utcnow() -> datetime:
     return datetime.now(UTC)
@@ -226,6 +226,7 @@ class _Turn:
             approval=approval,
             owner_notes=owner_notes or [],
             state=state,
+            booked=booked(self.writes),
         )
 
 
@@ -246,8 +247,11 @@ class TurnProcessor:
         channels: Mapping[ChannelName, Channel],
         settings: TurnSettings,
         clock: Callable[[], datetime] = _utcnow,
+        queue: TaskQueue | None = None,
     ) -> None:
+        """`queue` takes the reminders of what a turn books; without one, none are kept."""
         self._store = store
+        self._queue = queue
         self._agent = agent
         self._classifier = classifier
         self._open_rezerve = open_rezerve
@@ -279,6 +283,7 @@ class TurnProcessor:
                         result = dataclasses.replace(result, state="active")
                 lease.check()
                 await self._store.commit_turn(conversation_id, result, self._clock())
+                await self._remind(conversation, result.booked)
                 await self._send_unsent(conversation)
                 conversation = await self._store.conversation(conversation_id)
 
@@ -300,6 +305,34 @@ class TurnProcessor:
             sent_id = await telegram.send(str(owner), note)
             await self._store.mark_relayed(note.seq, sent_id, owner, conversation.id, self._clock())
 
+    async def _remind(
+        self, conversation: ConversationRecord, booked: list[tuple[str, datetime]]
+    ) -> None:
+        """Spec §7.7: a reminder a day ahead of what this turn booked or moved,
+        on Telegram, when that day ahead is still at least an hour away."""
+        if self._queue is None or conversation.channel != "telegram":
+            return
+        now = self._clock()
+        for booking_id, starts_at in booked:
+            at = starts_at - REMIND_BEFORE
+            if at - now < MIN_NOTICE:
+                continue
+            kept = await self._store.add_reminder(
+                booking_id,
+                starts_at,
+                conversation.business_slug,
+                conversation.customer_ref,
+                int(conversation.channel_user_id),
+                now,
+            )
+            if not kept:
+                continue
+            try:
+                await self._queue.enqueue_reminder(booking_id, starts_at, at)
+            except Exception:
+                # The booking is made either way; only its reminder is lost.
+                log.exception("Could not schedule the reminder for booking %s", booking_id)
+
     async def _owner_chat(self, business_slug: str) -> int | None:
         """The business owner's Telegram chat, if one is linked and Telegram is on."""
         if "telegram" not in self._channels:
```

In `src/assistant/internal.py`:

```diff
--- a/src/assistant/internal.py
+++ b/src/assistant/internal.py
@@ -8,12 +8,14 @@ account, so every call to them is refused there too.
 
 import asyncio
 from collections.abc import Mapping
+from datetime import datetime
 from typing import Annotated, Any
 
 from fastapi import APIRouter, Depends, HTTPException, Request, Response
 from google.auth.exceptions import GoogleAuthError, TransportError
 from google.auth.transport.requests import Request as GoogleRequest
 from google.oauth2 import id_token
+from pydantic import BaseModel
 
 from assistant.conversation.lease import LeaseBusy
 from assistant.services import AppServices, app_services
@@ -75,3 +77,14 @@ async def turn(conversation_id: int, services: Services) -> Response:
         # Another handler has the conversation: Cloud Tasks tries again later.
         raise HTTPException(status_code=429, detail="busy") from None
     return Response(status_code=204)
+
+
+class DueReminder(BaseModel):
+    booking_id: str
+    starts_at: datetime
+
+
+@router.post("/reminder", status_code=204, dependencies=[Depends(from_cloud_tasks)])
+async def reminder(due: DueReminder, services: Services) -> Response:
+    await services.send_reminder(due.booking_id, due.starts_at)
+    return Response(status_code=204)
```

In `src/assistant/services.py`:

```diff
--- a/src/assistant/services.py
+++ b/src/assistant/services.py
@@ -2,6 +2,7 @@
 
 from collections.abc import Awaitable, Callable, Mapping
 from dataclasses import dataclass
+from datetime import datetime
 from typing import Any, cast
 
 from fastapi import Request
@@ -28,6 +29,8 @@ class AppServices:
     verify_token: TokenVerifier
     # None while no bot token is set: the Telegram webhook is off.
     telegram: TelegramApi | None
+    # Sends a booking's reminder: what a reminder task runs.
+    send_reminder: Callable[[str, datetime], Awaitable[None]]
 
 
 def app_services(request: Request) -> AppServices:
```

In `src/assistant/main.py`, the queue is built before the processor, bound late:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -18,13 +18,14 @@ from assistant.channels.base import Channel, ChannelName
 from assistant.channels.telegram import TelegramApi, TelegramChannel
 from assistant.config import Settings
 from assistant.conversation.store import Store
-from assistant.conversation.turn import OpenRezerve, TurnProcessor, TurnSettings
+from assistant.conversation.turn import TurnProcessor, TurnSettings
 from assistant.db.session import create_engine, session_factory
-from assistant.mcp import connect
+from assistant.mcp import OpenRezerve, connect
 from assistant.queue.base import TaskQueue
 from assistant.queue.cloud_tasks import CloudTasksQueue, TaskCreator
 from assistant.queue.local import LocalTaskQueue
 from assistant.ratelimit import SlidingWindow
+from assistant.reminders import Reminders
 from assistant.services import AppServices, TokenVerifier
 
 
@@ -56,17 +57,10 @@ def create_app(
                 )
                 telegram = TelegramApi(config.telegram_bot_token.get_secret_value(), client)
                 channels["telegram"] = TelegramChannel(telegram)
-            processor = TurnProcessor(
-                store=store,
-                agent=build_agent(chosen),
-                classifier=ReplyClassifier(chosen),
-                open_rezerve=open_rezerve
-                or partial(
-                    connect, config.rezerve_mcp_url, config.assistant_mcp_token.get_secret_value()
-                ),
-                channels=channels,
-                settings=TurnSettings.from_settings(config),
+            open_session: OpenRezerve = open_rezerve or partial(
+                connect, config.rezerve_mcp_url, config.assistant_mcp_token.get_secret_value()
             )
+            reminders = Reminders(store, open_session, channels.get("telegram"))
             queue: TaskQueue
             if config.queue_backend == "cloud_tasks":
                 if config.gcp_project is None or config.tasks_invoker_sa is None:
@@ -79,13 +73,28 @@ def create_app(
                     project=config.gcp_project,
                     region=config.gcp_region,
                     turns_queue=config.tasks_queue_turns,
+                    reminders_queue=config.tasks_queue_reminders,
                     base_url=config.public_base_url,
                     invoker=config.tasks_invoker_sa,
                 )
             else:
-                local = LocalTaskQueue(processor.process)
+                # The processor needs the queue for reminders, and the local
+                # queue needs the processor for turns: bound on first use.
+                async def answer(conversation_id: int) -> None:
+                    await processor.process(conversation_id)
+
+                local = LocalTaskQueue(answer, remind=reminders.send)
                 stack.push_async_callback(local.aclose)
                 queue = local
+            processor = TurnProcessor(
+                store=store,
+                agent=build_agent(chosen),
+                classifier=ReplyClassifier(chosen),
+                open_rezerve=open_session,
+                channels=channels,
+                settings=TurnSettings.from_settings(config),
+                queue=queue,
+            )
             app.state.services = AppServices(
                 settings=config,
                 store=store,
@@ -96,6 +105,7 @@ def create_app(
                 process_turn=processor.process,
                 verify_token=verify_token or internal.verify_google_token,
                 telegram=telegram,
+                send_reminder=reminders.send,
             )
             yield
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/integration/test_reminders.py tests/integration/test_turn.py tests/unit/test_cloud_tasks.py tests/unit/test_local_queue.py tests/integration/test_internal.py -q`
Expected: every test passes.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `491 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/reminders.py src/assistant/db/alembic/versions/0007_reminders.py \
  src/assistant/queue/base.py src/assistant/db/models.py src/assistant/conversation/store.py \
  src/assistant/conversation/turn.py src/assistant/mcp.py src/assistant/queue/local.py \
  src/assistant/queue/cloud_tasks.py src/assistant/internal.py src/assistant/services.py \
  src/assistant/main.py tests/fakes/channels.py tests/fakes/queue.py tests/conftest.py \
  tests/integration/test_reminders.py tests/integration/test_turn.py tests/unit/test_cloud_tasks.py \
  tests/unit/test_local_queue.py tests/integration/test_internal.py
git commit -m "feat(reminders): a day before a booking made or moved on Telegram

Spec §7.7: after a turn commits, each booking it made or moved on Telegram gets
a reminder task for 24 hours before the start. When it fires, the booking is
read from Rezerve: nothing is sent for one moved, cancelled or never paid.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Rezerve's "kapora received" event

Spec §5 and §6.5. Rezerve posts `booking.confirmed` to `/events/rezerve` when a kapora is paid for an assistant booking. The agent's side is fixed here, and plan 4b's Rezerve side signs the same way:
- **Signature:** `X-Rezerve-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw body>">`, under `ASSISTANT_EVENT_SECRET`.
- **Freshness:** `X-Rezerve-Timestamp`, in Unix seconds, must be within five minutes either way.
- **Body:** `type`, `booking_id`, `business_slug`, `customer_ref`, `service_name` and `local_label`.

What the endpoint does:
- **A good event** is acted on once (`processed_events`). It puts "Kaporanız alındı, randevunuz onaylandı ✅ …" in the customer's latest conversation and queues its turn, which sends it.
- **Another kind of event** is ignored.
- **A malformed one** gets a 422, and an unsigned or stale one a 401.
- **Without a secret,** the endpoint is a 404.

**Files:**
- Create: `src/assistant/events.py`, `src/assistant/db/alembic/versions/0008_processed_events.py`
- Modify: `src/assistant/db/models.py`, `src/assistant/conversation/store.py`, `src/assistant/main.py`, `tests/conftest.py`
- Test: `tests/integration/test_events.py`

**Interfaces:**
- Consumes: `Store.add_reply` (Task 9); `AppServices.queue`.
- Produces: `signature(secret, timestamp, body) -> str`, `CONFIRMED`, `BookingConfirmed` and `router`, in `assistant.events`.
- Produces: `Store.first_seen(event_id, now) -> bool` and `Store.latest_conversation(business_slug, customer_ref) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_events.py`:

```python
"""Rezerve's "kapora received" event (spec §6.5), through the whole service in
process: signed, inside its window, told to the customer once, on their
channel."""

import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from assistant.channels.base import InboundMessage
from assistant.config import Settings
from assistant.conversation.store import Store, TurnResult
from assistant.events import signature
from assistant.identity import customer_ref
from assistant.main import create_app
from assistant.queue.local import LocalTaskQueue
from assistant.services import AppServices
from tests.fakes.models import Script
from tests.fakes.rezerve import FakeRezerve
from tests.fakes.telegram import FakeTelegram

SECRET = "event-secret"
CHAT = 4242
REF = customer_ref("s", "telegram", str(CHAT))
CONFIRMED = "Kaporanız alındı, randevunuz onaylandı ✅ Cilt Bakımı · Cumartesi 7 Kasım 15:30"


def confirmed(booking_id: str = "b-1", ref: str = REF) -> dict[str, Any]:
    return {
        "type": "booking.confirmed",
        "booking_id": booking_id,
        "business_slug": "demo",
        "customer_ref": ref,
        "service_name": "Cilt Bakımı",
        "local_label": "Cumartesi 7 Kasım 15:30",
    }


class Rezerve:
    """Rezerve's side: events, signed the way lib/assistant/events.ts signs them."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient, telegram: FakeTelegram) -> None:
        self.app = app
        self.client = client
        self.telegram = telegram

    async def send(
        self,
        event: dict[str, Any],
        *,
        secret: str = SECRET,
        at: float | None = None,
        signed: bool = True,
    ) -> httpx.Response:
        body = json.dumps(event).encode()
        timestamp = str(int(at if at is not None else time.time()))
        headers = {"Content-Type": "application/json", "X-Rezerve-Timestamp": timestamp}
        if signed:
            headers["X-Rezerve-Signature"] = signature(secret, timestamp, body)
        return await self.client.post("/events/rezerve", content=body, headers=headers)

    async def settle(self) -> None:
        services = cast(AppServices, self.app.state.services)
        await cast(LocalTaskQueue, services.queue).drain()


@pytest.fixture
def event_overrides() -> dict[str, Any]:
    return {}


@pytest.fixture
async def rezerve(
    database_url: str, store: Store, event_overrides: dict[str, Any]
) -> AsyncIterator[Rezerve]:
    fake = FakeRezerve()
    telegram = FakeTelegram()
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=database_url,
        assistant_mcp_token="t",
        customer_ref_secret="s",
        telegram_bot_token="123456:secret-token",
        telegram_webhook_secret="webhook-secret",
        **{"assistant_event_secret": SECRET} | event_overrides,
    )
    app = create_app(
        settings,
        model=Script().model,
        open_rezerve=lambda slug, ref: fake.session(),
        telegram_transport=telegram.transport,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
            yield Rezerve(app, client, telegram)


async def talked_on_telegram(store: Store) -> None:
    """A Telegram customer whose conversation is answered and waiting."""
    now = datetime.now(UTC)
    recorded = await store.record_inbound(
        InboundMessage("demo", "telegram", str(CHAT), "update-1", "merhaba"), REF, now
    )
    assert recorded.seq is not None
    await store.commit_turn(
        recorded.conversation_id, TurnResult(processed=[recorded.seq], replies=[]), now
    )


async def test_a_paid_kapora_is_told_to_the_customer_on_their_channel(
    rezerve: Rezerve, store: Store
) -> None:
    await talked_on_telegram(store)
    response = await rezerve.send(confirmed())
    await rezerve.settle()
    assert response.status_code == 202
    assert rezerve.telegram.texts(CHAT) == [CONFIRMED]


async def test_the_same_event_twice_is_told_once(rezerve: Rezerve, store: Store) -> None:
    await talked_on_telegram(store)
    await rezerve.send(confirmed())
    await rezerve.send(confirmed())
    await rezerve.settle()
    assert rezerve.telegram.texts(CHAT) == [CONFIRMED]


@pytest.mark.parametrize(
    "tamper",
    ["unsigned", "wrong secret", "five minutes and more old", "from the future"],
)
async def test_only_a_fresh_event_signed_with_the_secret_is_heard(
    rezerve: Rezerve, store: Store, tamper: str
) -> None:
    await talked_on_telegram(store)
    sends = {
        "unsigned": lambda: rezerve.send(confirmed(), signed=False),
        "wrong secret": lambda: rezerve.send(confirmed(), secret="guessed"),
        "five minutes and more old": lambda: rezerve.send(confirmed(), at=time.time() - 301),
        "from the future": lambda: rezerve.send(confirmed(), at=time.time() + 301),
    }
    response = await sends[tamper]()
    await rezerve.settle()
    assert response.status_code == 401
    assert rezerve.telegram.calls == []


async def test_a_customer_with_no_conversation_has_nothing_to_hear(rezerve: Rezerve) -> None:
    response = await rezerve.send(confirmed(ref=customer_ref("s", "telegram", "999")))
    assert response.status_code == 202
    assert rezerve.telegram.calls == []


async def test_an_event_of_another_kind_is_ignored(rezerve: Rezerve, store: Store) -> None:
    await talked_on_telegram(store)
    response = await rezerve.send({"type": "booking.refunded", "booking_id": "b-1"})
    assert response.status_code == 202
    assert rezerve.telegram.calls == []


@pytest.mark.parametrize("event_overrides", [{"assistant_event_secret": None}])
async def test_without_a_secret_there_is_no_endpoint(rezerve: Rezerve) -> None:
    assert (await rezerve.send(confirmed())).status_code == 404


async def test_a_signed_event_missing_its_details_is_refused(rezerve: Rezerve) -> None:
    response = await rezerve.send({"type": "booking.confirmed", "booking_id": "b-1"})
    assert response.status_code == 422
```

In `tests/conftest.py`:

```diff
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -33,7 +33,8 @@ async def store(database_url: str) -> AsyncIterator[Store]:
         await connection.execute(
             sa.text(
                 "TRUNCATE conversations, messages, pending_approvals, agent_runs, "
-                "telegram_chats, business_config, owner_relays, reminders RESTART IDENTITY CASCADE"
+                "telegram_chats, business_config, owner_relays, reminders, processed_events "
+                "RESTART IDENTITY CASCADE"
             )
         )
     yield Store(session_factory(engine))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/integration/test_events.py -q`
Expected: collection fails: `ModuleNotFoundError: No module named 'assistant.events'`.

- [ ] **Step 3: Write the endpoint**

Create `src/assistant/db/alembic/versions/0008_processed_events.py`:

```python
"""Events from Rezerve already acted on, and a customer lookup for them.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("conversations_customer", "conversations", ["business_slug", "customer_ref"])


def downgrade() -> None:
    op.drop_index("conversations_customer", table_name="conversations")
    op.drop_table("processed_events")
```

In `src/assistant/db/models.py`:

```diff
--- a/src/assistant/db/models.py
+++ b/src/assistant/db/models.py
@@ -51,6 +51,8 @@ class Conversation(Base):
             postgresql_where=sa.text("state <> 'closed'"),
         ),
         sa.Index("conversations_last_message_at", "last_message_at"),
+        # Rezerve's events name a customer by business and ref.
+        sa.Index("conversations_customer", "business_slug", "customer_ref"),
     )
 
 
@@ -208,3 +210,12 @@ class Reminder(Base):
     sent_at: Mapped[datetime | None]
     sent_message_id: Mapped[str | None] = mapped_column(sa.Text)
     created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
+
+
+class ProcessedEvent(Base):
+    """An event from Rezerve already acted on: a resent one is ignored."""
+
+    __tablename__ = "processed_events"
+
+    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
+    received_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
```

In `src/assistant/conversation/store.py`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -20,6 +20,7 @@ from assistant.db.models import (
     Message,
     OwnerRelay,
     PendingApproval,
+    ProcessedEvent,
     Reminder,
     TelegramChat,
 )
@@ -673,6 +674,33 @@ class Store:
                 .values(sent_at=now, sent_message_id=sent_message_id)
             )
 
+    async def first_seen(self, event_id: str, now: datetime) -> bool:
+        """Whether this event is new; it counts as seen from now on."""
+        async with self._sessions.begin() as session:
+            await session.execute(
+                sa.delete(ProcessedEvent).where(ProcessedEvent.received_at < now - RETENTION)
+            )
+            added = await session.execute(
+                pg_insert(ProcessedEvent)
+                .values(id=event_id, received_at=now)
+                .on_conflict_do_nothing()
+                .returning(ProcessedEvent.id)
+            )
+            return added.scalar_one_or_none() is not None
+
+    async def latest_conversation(self, business_slug: str, customer_ref: str) -> int | None:
+        """The customer's most recent conversation with this business, open or not."""
+        async with self._sessions() as session:
+            return await session.scalar(
+                sa.select(Conversation.id)
+                .where(
+                    Conversation.business_slug == business_slug,
+                    Conversation.customer_ref == customer_ref,
+                )
+                .order_by(Conversation.last_message_at.desc())
+                .limit(1)
+            )
+
     async def decisions(self, conversation_id: int) -> list[tuple[str, Decision | None]]:
         """Every write proposed in the conversation, and how it was answered."""
         async with self._sessions() as session:
```

Create `src/assistant/events.py`:

```python
"""Rezerve's events (spec §5, §6.5). Each is signed with HMAC-SHA256 over its
timestamp and body, and refused outside a five-minute window, so a request
seen once cannot be sent again later.

Best effort on both sides: Rezerve sends each event once, and the
confirmation page and email have already told the customer.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from assistant.services import AppServices, app_services

TIMESTAMP_HEADER = "X-Rezerve-Timestamp"
SIGNATURE_HEADER = "X-Rezerve-Signature"
WINDOW_SECONDS = 300
CONFIRMED = "Kaporanız alındı, randevunuz onaylandı ✅ {service_name} · {local_label}"

type Services = Annotated[AppServices, Depends(app_services)]

router = APIRouter(prefix="/events", include_in_schema=False)


def signature(secret: str, timestamp: str, body: bytes) -> str:
    """What Rezerve puts in X-Rezerve-Signature."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


class Event(BaseModel):
    type: str


class BookingConfirmed(BaseModel):
    """A kapora paid: Rezerve confirmed an assistant booking."""

    type: Literal["booking.confirmed"]
    booking_id: str
    business_slug: str
    customer_ref: str
    service_name: str
    local_label: str


@router.post("/rezerve", status_code=202)
async def rezerve_event(request: Request, services: Services) -> dict[str, str]:
    secret = services.settings.assistant_event_secret
    if secret is None:
        raise HTTPException(status_code=404)
    body = await request.body()
    timestamp = request.headers.get(TIMESTAMP_HEADER, "")
    given = request.headers.get(SIGNATURE_HEADER, "")
    fresh = timestamp.isdigit() and abs(time.time() - int(timestamp)) <= WINDOW_SECONDS
    expected = signature(secret.get_secret_value(), timestamp, body)
    if not (fresh and hmac.compare_digest(given.encode(), expected.encode())):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        if Event.model_validate_json(body).type != "booking.confirmed":
            return {"status": "ignored"}
        event = BookingConfirmed.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="malformed event") from error
    now = datetime.now(UTC)
    if not await services.store.first_seen(f"{event.type}:{event.booking_id}", now):
        return {"status": "duplicate"}
    conversation_id = await services.store.latest_conversation(
        event.business_slug, event.customer_ref
    )
    if conversation_id is None:
        return {"status": "no_conversation"}
    text = CONFIRMED.format(service_name=event.service_name, local_label=event.local_label)
    seq = await services.store.add_reply(conversation_id, text)
    # The turn sends what waits unsent; there is nothing else to answer.
    await services.queue.enqueue_turn(conversation_id, seq)
    return {"status": "accepted"}
```

In `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -10,7 +10,7 @@ from fastapi.middleware.cors import CORSMiddleware
 from google.cloud import tasks_v2
 from pydantic_ai.models import Model
 
-from assistant import internal
+from assistant import events, internal
 from assistant.agent.agent import build_agent, build_model
 from assistant.agent.approvals import ReplyClassifier
 from assistant.channels import telegram_webhook, web
@@ -120,6 +120,7 @@ def create_app(
     )
     app.include_router(web.router)
     app.include_router(telegram_webhook.router)
+    app.include_router(events.router)
     app.include_router(internal.router)
 
     @app.get("/health")
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/integration/test_events.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Run every check**

Run, reading each output:
- `uv run pytest -q`: `501 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/assistant/events.py src/assistant/db/alembic/versions/0008_processed_events.py \
  src/assistant/db/models.py src/assistant/conversation/store.py src/assistant/main.py \
  tests/conftest.py tests/integration/test_events.py
git commit -m "feat(events): Rezerve's 'kapora received', told to the customer once

Spec §6.5: POST /events/rezerve, signed with HMAC-SHA256 over the timestamp
and body and refused outside five minutes. booking.confirmed puts 'Kaporanız
alındı, randevunuz onaylandı ✅' in the customer's latest conversation, once.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 12: Traces, to Langfuse

Spec §7.10, with the two rulings above: httpx is not traced, and customer text stays out unless `TRACE_CONTENT=true`.
- **Where traces go:** with `OTEL_TRACES_ENDPOINT` set, Logfire's SDK exports over OTLP/HTTP to that endpoint, with `OTEL_HEADERS`, and sends nothing to Logfire itself. Pydantic AI's model calls and FastAPI's requests are traced.
- **The turn's span:** each turn is a span named `turn`, carrying the business, the channel, the model, the tokens and the outcome. Its id goes on the turn's `agent_runs` row, which outlives Langfuse's 30 days.
- **Session and user:** the conversation is Langfuse's session (`langfuse.session.id`) and the customer ref its user (`langfuse.user.id`). They go on the turn's span, and as baggage onto every span inside it, the model calls' included.
- **Without an endpoint:** the span is OpenTelemetry's no-op, and nothing is configured.

**Files:**
- Create: `src/assistant/observability.py`
- Modify: `src/assistant/conversation/store.py`, `src/assistant/conversation/turn.py`, `src/assistant/main.py`, and `pyproject.toml` and `uv.lock`, through `uv add`
- Test: `tests/unit/test_observability.py`, `tests/integration/test_turn.py`

**Interfaces:**
- Consumes: `Settings.otel_traces_endpoint`, `otel_headers` and `trace_content` (Task 1).
- Produces, in `assistant.observability`:
  - `otlp_headers(raw) -> dict[str, str]`;
  - `configure(settings, app) -> bool`;
  - `tagged(values: dict[str, str])`, a context manager.
- Produces: `RunRecord.trace_id: str | None = None`, which `commit_turn` stores and `runs` reads back.
- Produces: in `turn.py`, `tracer` and the span named `"turn"`, with the attributes `langfuse.session.id`, `langfuse.user.id`, `langfuse.trace.name`, `business.slug`, `channel`, and `turn.outcome` / `turn.model_used` / `turn.models_tried` / `turn.requests` / `turn.input_tokens` / `turn.output_tokens`.

- [ ] **Step 1: Add Logfire, with its FastAPI extra, and Pydantic AI's Logfire extra**

Run: `uv add "logfire[fastapi]" "pydantic-ai-slim[google,groq,logfire,mcp]>=2.56.0"`
Expected:
- `pyproject.toml` lists `logfire[fastapi]>=5.2.0`, and Pydantic AI with the `logfire` extra;
- `uv.lock` resolves logfire 5.2.0 and opentelemetry-exporter-otlp-proto-http 1.45.1;
- `uv run python -c "import opentelemetry.instrumentation.fastapi"` exits 0.

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_observability.py`:

```python
from typing import Any

import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from assistant import observability
from assistant.config import Settings
from assistant.observability import otlp_headers


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, assistant_mcp_token="t", customer_ref_secret="s", **values)  # pyright: ignore[reportCallIssue]


def test_headers_are_read_the_way_otel_writes_them() -> None:
    # Langfuse's Basic auth is base64, padded with "=": only the first "=" splits.
    raw = "Authorization=Basic cGs6c2s=,x-langfuse-ingestion-version=4"
    assert otlp_headers(raw) == {
        "Authorization": "Basic cGs6c2s=",
        "x-langfuse-ingestion-version": "4",
    }


def test_without_an_endpoint_nothing_is_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("logfire was configured")

    monkeypatch.setattr(observability.logfire, "configure", refuse)
    assert observability.configure(settings(), FastAPI()) is False


def test_traces_go_to_the_endpoint_and_nowhere_else(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Exporter:
        def __init__(self, *, endpoint: str, headers: dict[str, str]) -> None:
            seen["exporter"] = {"endpoint": endpoint, "headers": headers}

        def shutdown(self) -> None: ...

    def record(name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> None:
            seen[name] = (args, kwargs)

        return call

    monkeypatch.setattr(observability, "OTLPSpanExporter", Exporter)
    for name in ("configure", "instrument_pydantic_ai", "instrument_fastapi"):
        monkeypatch.setattr(observability.logfire, name, record(name))
    app = FastAPI()
    endpoint = "https://cloud.langfuse.com/api/public/otel/v1/traces"

    traced = observability.configure(
        settings(
            otel_traces_endpoint=endpoint,
            otel_headers="Authorization=Basic cGs6c2s=,x-langfuse-ingestion-version=4",
        ),
        app,
    )

    assert traced is True
    assert seen["exporter"] == {
        "endpoint": endpoint,
        "headers": {"Authorization": "Basic cGs6c2s=", "x-langfuse-ingestion-version": "4"},
    }
    _, options = seen["configure"]
    assert options["send_to_logfire"] is False
    [processor] = options["additional_span_processors"]
    assert isinstance(processor, BatchSpanProcessor)
    # What customers write is personal data: out of the traces unless asked for.
    assert seen["instrument_pydantic_ai"] == ((), {"include_content": False})
    assert seen["instrument_fastapi"] == ((app,), {"excluded_urls": "/health"})
```

In `tests/integration/test_turn.py`, an in-memory span exporter, set once for the session, and one test:

```diff
--- a/tests/integration/test_turn.py
+++ b/tests/integration/test_turn.py
@@ -6,6 +6,10 @@ from typing import Any
 from uuid import uuid4
 
 import pytest
+from opentelemetry import trace
+from opentelemetry.sdk.trace import TracerProvider
+from opentelemetry.sdk.trace.export import SimpleSpanProcessor
+from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
 from pydantic_ai import (
     ModelHTTPError,
     ModelMessage,
@@ -724,3 +728,36 @@ async def test_a_move_is_reminded_at_its_new_time(store: Store) -> None:
     await turn.say("randevumu bir gün sonraya alalım")
     await turn.say("evet")
     assert turn.queue.reminders == [(booking.id, later, later - timedelta(hours=24))]
+
+
+@pytest.fixture(scope="session")
+def spans() -> InMemorySpanExporter:
+    """Every span from here on, kept in memory: OpenTelemetry takes one
+    provider per process, so this is set once."""
+    exporter = InMemorySpanExporter()
+    provider = TracerProvider()
+    provider.add_span_processor(SimpleSpanProcessor(exporter))
+    trace.set_tracer_provider(provider)
+    return exporter
+
+
+async def test_each_turn_is_a_trace_whose_id_its_run_keeps(
+    store: Store, spans: InMemorySpanExporter
+) -> None:
+    # Spec §7.10: one trace per turn, and its id on agent_runs, which outlives
+    # Langfuse's 30 days.
+    spans.clear()
+    turn = Harness(store, Script(says("Merhaba!")))
+    await turn.say("merhaba")
+
+    [span] = [span for span in spans.get_finished_spans() if span.name == "turn"]
+    [run] = await store.runs(turn.conversation_id)
+    assert span.context is not None
+    assert run.trace_id == format(span.context.trace_id, "032x")
+    attributes = dict(span.attributes or {})
+    assert attributes["langfuse.session.id"] == str(turn.conversation_id)
+    assert attributes["langfuse.user.id"] == REF
+    assert attributes["business.slug"] == "demo"
+    assert attributes["channel"] == "web"
+    assert attributes["turn.outcome"] == "reply"
+    assert attributes["turn.model_used"] == "script"
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/unit/test_observability.py -q`
Expected: collection fails: `ImportError: cannot import name 'observability' from 'assistant'`.

- [ ] **Step 4: Write the configuration and the turn's span**

Create `src/assistant/observability.py`:

```python
"""Traces (spec §7.10): the turns, Pydantic AI's model calls and FastAPI's
requests, over OTLP/HTTP to Langfuse Cloud EU, and none to Logfire itself.

Two things stay out. httpx is not instrumented, since Telegram's URLs carry
the bot token. And what customers wrote is left out unless TRACE_CONTENT is
set, since it is personal data and Langfuse keeps it for 30 days.
"""

from collections.abc import Generator
from contextlib import contextmanager
from urllib.parse import unquote

import logfire
from fastapi import FastAPI
from opentelemetry import baggage, context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from assistant.config import Settings

SERVICE = "rezerve-assistant"


def otlp_headers(raw: str) -> dict[str, str]:
    """OTEL_EXPORTER_OTLP_HEADERS' format: "key=value,key=value", values
    URL-encoded. Only the first "=" of a pair splits it: base64 ends in "="."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        key, found, value = pair.partition("=")
        if found and key.strip():
            headers[key.strip()] = unquote(value.strip())
    return headers


def configure(settings: Settings, app: FastAPI) -> bool:
    """Send traces, if there is somewhere to send them; returns whether it will."""
    if settings.otel_traces_endpoint is None:
        return False
    raw = settings.otel_headers.get_secret_value() if settings.otel_headers else ""
    exporter = OTLPSpanExporter(endpoint=settings.otel_traces_endpoint, headers=otlp_headers(raw))
    logfire.configure(
        send_to_logfire=False,
        service_name=SERVICE,
        console=False,
        metrics=False,
        additional_span_processors=[BatchSpanProcessor(exporter)],
    )
    logfire.instrument_pydantic_ai(include_content=settings.trace_content)
    logfire.instrument_fastapi(app, excluded_urls="/health")
    return True


@contextmanager
def tagged(values: dict[str, str]) -> Generator[None]:
    """Values every span started inside carries, the model calls' too:
    Langfuse reads a trace's session and user from any of its spans."""
    current = context.get_current()
    for key, value in values.items():
        current = baggage.set_baggage(key, value, current)
    token = context.attach(current)
    try:
        yield
    finally:
        context.detach(token)
```

In `src/assistant/conversation/store.py`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -104,6 +104,8 @@ class RunRecord:
     tool_calls: int
     tool_errors: int
     outcome: Outcome
+    # The turn's trace, kept past Langfuse's 30 days (spec §7.10).
+    trace_id: str | None = None
 
 
 @dataclass(frozen=True)
@@ -404,6 +406,7 @@ class Store:
                         tool_calls=run.tool_calls,
                         tool_errors=run.tool_errors,
                         outcome=run.outcome,
+                        trace_id=run.trace_id,
                     )
                 )
             if result.processed:
@@ -433,6 +436,7 @@ class Store:
                     tool_calls=row.tool_calls,
                     tool_errors=row.tool_errors,
                     outcome=cast(Outcome, row.outcome),
+                    trace_id=row.trace_id,
                 )
                 for row in rows.scalars()
             ]
```

In `src/assistant/conversation/turn.py`, `process` opens the span, sets the values the whole trace carries, and records each batch on it:

```diff
--- a/src/assistant/conversation/turn.py
+++ b/src/assistant/conversation/turn.py
@@ -15,6 +15,8 @@ from collections.abc import Callable, Mapping, Sequence
 from dataclasses import dataclass, field
 from datetime import UTC, datetime, timedelta
 
+from opentelemetry import trace
+from opentelemetry.trace import Span
 from pydantic_ai import (
     Agent,
     AgentRunResult,
@@ -73,10 +75,13 @@ from assistant.conversation.store import (
     TurnResult,
 )
 from assistant.mcp import BusinessInfo, CompletedWrite, OpenRezerve, RezerveSession
+from assistant.observability import tagged
 from assistant.queue.base import TaskQueue
 from assistant.reminders import MIN_NOTICE, REMIND_BEFORE, booked
 
 log = logging.getLogger(__name__)
+# A no-op until observability.configure sets an exporter.
+tracer = trace.get_tracer("assistant.turn")
 
 # Calls code may send back to the model in one turn — a service id it made up,
 # an email it forgot to ask for — before the business is handed the customer.
@@ -216,6 +221,7 @@ class _Turn:
             tool_calls=meter.tool_calls,
             tool_errors=meter.tool_errors,
             outcome=outcome,
+            trace_id=_current_trace_id(),
         )
         return TurnResult(
             processed=self.processed,
@@ -230,6 +236,28 @@ class _Turn:
         )
 
 
+def _current_trace_id() -> str | None:
+    span = trace.get_current_span().get_span_context()
+    return format(span.trace_id, "032x") if span.is_valid else None
+
+
+def _describe(span: Span, result: TurnResult) -> None:
+    """Spec §7.10: what the turn did, on its trace."""
+    run = result.run
+    if run is None:
+        return
+    span.set_attributes(
+        {
+            "turn.outcome": run.outcome,
+            "turn.model_used": run.model_used or "",
+            "turn.models_tried": run.models_tried,
+            "turn.requests": run.requests,
+            "turn.input_tokens": run.input_tokens,
+            "turn.output_tokens": run.output_tokens,
+        }
+    )
+
+
 def _decision(resolution: Resolution) -> Decision:
     if resolution == "approve":
         return "approved"
@@ -264,6 +292,10 @@ class TurnProcessor:
 
     async def process(self, conversation_id: int) -> None:
         """Raises LeaseBusy when another handler holds the conversation."""
+        with tracer.start_as_current_span("turn") as span:
+            await self._process(conversation_id, span)
+
+    async def _process(self, conversation_id: int, span: Span) -> None:
         settings = self._settings
         async with Lease(
             self._store,
@@ -272,20 +304,38 @@ class TurnProcessor:
             renew_every=settings.lease_renew,
         ) as lease:
             conversation = await self._store.conversation(conversation_id)
+            trace_values = {
+                "langfuse.session.id": str(conversation.id),
+                "langfuse.user.id": conversation.customer_ref,
+            }
+            span.set_attributes(
+                {
+                    **trace_values,
+                    "langfuse.trace.name": "turn",
+                    "business.slug": conversation.business_slug,
+                    "channel": conversation.channel,
+                }
+            )
+            with tagged(trace_values):
+                await self._answer_all(conversation, span, lease)
+
+    async def _answer_all(self, conversation: ConversationRecord, span: Span, lease: Lease) -> None:
+        """Every unprocessed message, oldest first, until none is left."""
+        await self._send_unsent(conversation)
+        while batch := await self._store.unprocessed_inbound(conversation.id):
+            if await self._owner_has(conversation):
+                result = self._relay(batch)
+            else:
+                result = await self._answer(conversation, batch, lease.check)
+                if conversation.state == "human" and result.state is None:
+                    # The owner's 24 hours are over, or no owner is linked now.
+                    result = dataclasses.replace(result, state="active")
+            lease.check()
+            await self._store.commit_turn(conversation.id, result, self._clock())
+            _describe(span, result)
+            await self._remind(conversation, result.booked)
             await self._send_unsent(conversation)
-            while batch := await self._store.unprocessed_inbound(conversation_id):
-                if await self._owner_has(conversation):
-                    result = self._relay(batch)
-                else:
-                    result = await self._answer(conversation, batch, lease.check)
-                    if conversation.state == "human" and result.state is None:
-                        # The owner's 24 hours are over, or no owner is linked now.
-                        result = dataclasses.replace(result, state="active")
-                lease.check()
-                await self._store.commit_turn(conversation_id, result, self._clock())
-                await self._remind(conversation, result.booked)
-                await self._send_unsent(conversation)
-                conversation = await self._store.conversation(conversation_id)
+            conversation = await self._store.conversation(conversation.id)
 
     async def _send_unsent(self, conversation: ConversationRecord) -> None:
         channel = self._channels[conversation.channel]
```

In `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -10,7 +10,7 @@ from fastapi.middleware.cors import CORSMiddleware
 from google.cloud import tasks_v2
 from pydantic_ai.models import Model
 
-from assistant import events, internal
+from assistant import events, internal, observability
 from assistant.agent.agent import build_agent, build_model
 from assistant.agent.approvals import ReplyClassifier
 from assistant.channels import telegram_webhook, web
@@ -118,6 +118,7 @@ def create_app(
         # The widget polls every second; one preflight per ten minutes is plenty.
         max_age=600,
     )
+    observability.configure(config, app)
     app.include_router(web.router)
     app.include_router(telegram_webhook.router)
     app.include_router(events.router)
```

- [ ] **Step 5: Run them to see them pass**

Run: `uv run pytest tests/unit/test_observability.py tests/integration/test_turn.py -q`
Expected: every test passes.

- [ ] **Step 6: See the real configuration run once**

Logfire is stubbed in the unit test, so run its real configuration once, against a closed port:

```bash
uv run python - <<'EOF'
from fastapi import FastAPI
from opentelemetry import trace
from assistant import observability
from assistant.config import Settings

settings = Settings(_env_file=None, assistant_mcp_token="t", customer_ref_secret="s",
                    otel_traces_endpoint="http://127.0.0.1:9/v1/traces",
                    otel_headers="Authorization=Basic cGs6c2s=,x-langfuse-ingestion-version=4")
print("configured:", observability.configure(settings, FastAPI()))
with observability.tagged({"langfuse.session.id": "7"}):
    with trace.get_tracer("check").start_as_current_span("turn") as span:
        print("valid:", span.get_span_context().is_valid,
              "session:", dict(span.attributes or {}).get("langfuse.session.id"))
EOF
```

Expected:
- `configured: True`, then `valid: True session: 7`;
- then the exporter's own retries and "Failed to export spans batch" lines, since port 9 is closed, with no traceback from the service.

- [ ] **Step 7: Run every check**

Run, reading each output:
- `uv run pytest -q`: `505 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat;
- `uv run pyright`: `0 errors`. `tagged` returns `Generator[None]`, since pyright reports the `Iterator` form deprecated for `@contextmanager`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/assistant/observability.py src/assistant/conversation/store.py \
  src/assistant/conversation/turn.py src/assistant/main.py tests/unit/test_observability.py \
  tests/integration/test_turn.py
git commit -m "feat(traces): each turn a trace in Langfuse, its id kept on agent_runs

Spec §7.10: Logfire's SDK exports over OTLP/HTTP to OTEL_TRACES_ENDPOINT and
nothing to Logfire. The conversation is the session, the customer ref the user.
httpx is not traced (Telegram's URLs hold the bot token), and what customers
wrote stays out unless TRACE_CONTENT=true.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: CI

Spec §10. Every push to `main` and every pull request runs the four checks, with the tests against a Postgres service container, and builds the image. The evals stay out, since they spend free-tier quota. `tests/evals/test_rezerve_db.py` already skips without Rezerve's eval database.

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
# Every push and pull request: lint, types, the test suite against Postgres,
# and the image. The evals stay out: they spend free-tier model quota.
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: assistant_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres -d assistant_test"
          --health-interval 2s
          --health-timeout 5s
          --health-retries 30
    env:
      TEST_DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/assistant_test
      PYDANTIC_AI_NO_BANNER: "1"
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10
        with:
          version: "0.13.1"
          enable-cache: true
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest -q

  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - uses: docker/build-push-action@v7
        with:
          context: .
          push: false
          tags: rezerve-assistant:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Run what the workflow runs, on a database as fresh as CI's**

```bash
psql postgresql://postgres:postgres@localhost:5433/postgres -c "create database assistant_ci_check"
uv sync --locked
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/assistant_ci_check uv run pytest -q
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/assistant_ci_check uv run alembic check
docker build -t rezerve-assistant:ci .
docker run --rm rezerve-assistant:ci python -c "import assistant.main, assistant.cli, google.cloud.tasks_v2, logfire; print('ok')"
psql postgresql://postgres:postgres@localhost:5433/postgres -c "drop database assistant_ci_check"
```

Expected:
- `uv sync --locked` changes nothing;
- `505 passed`, with migrations 0001–0008 run from zero;
- `alembic check` prints `No new upgrade operations detected.`;
- the image builds, and prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint, types, the tests on Postgres, and the image, on every push and PR

The evals stay out: they spend free-tier model quota.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The README, and the settings template

The README's status and "How a message is answered" now cover Telegram, budgets and the owner, and a new "In production" section is the reference for what tasks 1–13 added. `.env.example` lists the new optional settings.

**Files:**
- Modify: `README.md`, `.env.example`

- [ ] **Step 1: Update the README**

````diff
--- a/README.md
+++ b/README.md
@@ -2,7 +2,7 @@
 
 An AI receptionist for Turkish local businesses. A customer writes in Turkish
 in the chat widget on a business's [Rezerve](https://github.com/Olbioss/rezerve)
-booking page (Telegram and WhatsApp come later). The assistant finds a free
+booking page or on Telegram (WhatsApp comes later). The assistant finds a free
 time and books it, taking a kapora when the service asks for one. It moves
 and cancels bookings under the business's policy, answers from the
 business's own information, and steps aside when it should not answer.
@@ -12,24 +12,32 @@ Rezerve's own booking code and its Postgres exclusion constraint. No write
 happens without the customer's "evet" to a confirmation that code rendered
 from the exact arguments that will run.
 
-**Status:** phase 3 of 5. The agent core, the web channel and the evals run locally.
+**Status:** phase 4 of 5. The agent, the widget and Telegram, and the evals run
+locally, and the service has what a public deployment needs: budgets, Cloud
+Tasks, the owner relay, reminders, Rezerve's kapora event and traces.
+Deploying it to Cloud Run is next.
 The design is in
 [docs/superpowers/specs/2026-10-14-rezerve-assistant-design.md](docs/superpowers/specs/2026-10-14-rezerve-assistant-design.md).
 
 ## How a message is answered
 
-1. **Stored once.** The widget posts a message and gets `202`; a redelivery
-   with the same `client_message_id` is stored once.
-2. **One handler per conversation.** A turn is queued and claims the
-   conversation's lease (a row, renewed while it works).
+1. **Stored once.** The widget, or Telegram's webhook, posts a message and
+   gets a 2xx; a redelivery (the same `client_message_id` or Telegram
+   `update_id`) is stored once.
+2. **One handler per conversation.** A turn is queued, in process or as a
+   Cloud Task, and claims the conversation's lease (a row, renewed while it
+   works).
 3. **One answer per burst.** Everything written since the last answer is
    handled in one Pydantic AI run with Rezerve's tools. Who the customer is
    travels in MCP headers, never in arguments the model writes.
 4. **Writes wait for a yes.** A write ends the run. Code shows the
    confirmation, and the customer's next message, written after the
    confirmation appeared, approves it, denies it, or changes the plan.
-5. **Every customer gets an answer.** If every model fails, the reply is a
-   link to the booking page.
+5. **Every customer gets an answer.** If every model fails, or a daily
+   budget is spent, the reply is a link to the booking page.
+6. **The owner can step in.** With an owner chat linked, a handoff goes to
+   the business owner on Telegram, who answers by replying and hands the
+   conversation back with `/bitir`.
 
 ## The race the confirmation gate had
 
@@ -88,7 +96,52 @@ uv run ruff check && uv run ruff format --check && uv run pyright
 ```
 
 The agent tests use scripted models and an in-process fake Rezerve: no
-network, no quota.
+network, no quota. CI (`.github/workflows/ci.yml`) runs the same checks
+against a Postgres service container, and builds the image.
+
+## In production
+
+The same service, with what a public demo needs:
+
+- **Configuration.** Every setting comes from one Secret Manager secret: a
+  JSON object keyed like the environment (`DATABASE_URL`,
+  `TELEGRAM_BOT_TOKEN`, …), mounted as a file that `CONFIG_FILE` names. The
+  environment still overrides it.
+- **Queue.** With `QUEUE_BACKEND=cloud_tasks`, each turn is a named Cloud
+  Task that calls the service back at `/internal/turn/{conversation}`. The
+  call must carry an OIDC token Google signed for `TASKS_INVOKER_SA`, with
+  `PUBLIC_BASE_URL` as its audience; anything else gets a 401 or a 403. A
+  conversation another handler holds answers 429, and Cloud Tasks retries.
+- **Budgets** (spec §7.5). 400 model requests a day across the service,
+  40,000 tokens a day per conversation, 20 messages per sender in 10
+  minutes, and 60 widget posts per client address. Past a daily cap the
+  customer gets the booking page; past the sender limit, one "biraz
+  yavaşlayalım", then silence until they slow down.
+- **Telegram.** `t.me/<bot>?start=<slug>` binds a chat to a business; a
+  later one switches it. The webhook checks Telegram's
+  `X-Telegram-Bot-Api-Secret-Token`. Photos and stickers get a "text only"
+  reply, and group chats are ignored.
+- **The owner relay.** `uv run assistant owners link <slug> <chat_id>`
+  sends a business's handoffs to its owner's Telegram chat; the owner learns
+  the chat id by sending `/id` to the bot. The customer's next messages reach
+  the owner, who answers by replying to them, and `/bitir`, or 24 hours,
+  hands the conversation back. With no owner chat, a handoff gives the
+  business's phone number.
+- **Reminders.** A booking made or moved on Telegram is reminded 24 hours
+  before it starts, by a Cloud Task. When it fires, it reads the booking from
+  Rezerve, and sends nothing if the booking was moved, cancelled or never
+  paid.
+- **Rezerve's kapora event.** `POST /events/rezerve`, signed with
+  `X-Rezerve-Signature: sha256=<HMAC-SHA256 of "<timestamp>.<body>">` under
+  `ASSISTANT_EVENT_SECRET`, and refused unless `X-Rezerve-Timestamp` is
+  within five minutes. A `booking.confirmed` event tells the customer, once:
+  "Kaporanız alındı, randevunuz onaylandı ✅".
+- **Traces.** With `OTEL_TRACES_ENDPOINT` and `OTEL_HEADERS` set, each turn
+  is a trace in Langfuse: the conversation as its session, the customer ref
+  as its user, the model, tokens and outcome. Its id is kept on `agent_runs`,
+  past Langfuse's 30 days. What customers wrote stays out unless
+  `TRACE_CONTENT=true`, and httpx is not traced: Telegram's URLs hold the
+  bot token.
 
 ## Evals
````

- [ ] **Step 2: Update the settings template**

```diff
--- a/.env.example
+++ b/.env.example
@@ -14,3 +14,13 @@ GROQ_API_KEY=
 # ALLOWED_WEB_ORIGINS=http://localhost:3000
 # REZERVE_MCP_URL=http://localhost:3000/api/mcp
 # DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/assistant
+
+# Telegram, optional: the bot's token from @BotFather, and any random string
+# of letters, digits, "_" and "-" for Telegram to send with every update.
+# TELEGRAM_BOT_TOKEN=
+# TELEGRAM_WEBHOOK_SECRET=
+# The same value as ASSISTANT_EVENT_SECRET in Rezerve's environment.
+# ASSISTANT_EVENT_SECRET=
+# Traces, optional; Langfuse Cloud EU, with the project's public and secret key.
+# OTEL_TRACES_ENDPOINT=https://cloud.langfuse.com/api/public/otel/v1/traces
+# OTEL_HEADERS=Authorization=Basic <base64 of public:secret>,x-langfuse-ingestion-version=4
```

- [ ] **Step 3: Run every check**

Run, reading each output:
- `uv run pytest -q`: `505 passed`;
- `uv run ruff check .`: `All checks passed!`;
- `uv run ruff format --check .`: no file to reformat. ruff formats the README's Python code blocks too;
- `uv run pyright`: `0 errors`.

- [ ] **Step 4: Commit**

```bash
git add README.md .env.example
git commit -m "docs: the service in production: Telegram, the owner relay, budgets, events, traces

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## What plan 4b does

Plan 4b is written once this plan is merged. It starts from this plan's interfaces:
- **GCP:** a setup script for you to run, covering:
  - the project, with a $1 budget alert;
  - the APIs;
  - Artifact Registry, keeping the last 3 images;
  - the `turns` and `reminders` queues, with spec §9's rates;
  - the runtime, invoker and deployer service accounts;
  - Workload Identity Federation for GitHub;
  - the one Secret Manager secret.
- **The database:** a Neon project for the agent's database. It uses Neon's direct endpoint, since the pooled one needs asyncpg's statement cache off.
- **The deploy workflow:** test, build, push, migrate, then `gcloud run deploy`, with the secret mounted at `CONFIG_FILE`.
- **Rezerve's side of the event (§6.5), on a branch:**
  - `confirmPaidBooking` signs and sends `booking.confirmed` for `source = 'assistant'` bookings;
  - Vercel gets `ASSISTANT_URL`, `ASSISTANT_MCP_TOKEN` and `ASSISTANT_EVENT_SECRET`.
- **Go-live checks:**
  - `scripts/set_webhook.py` registers Telegram's webhook;
  - one real request settles `TRUSTED_PROXY_HOPS`;
  - a Telegram booking on the demo business;
  - a trace in Langfuse;
  - a forced 500, retried by Cloud Tasks;
  - a reminder task in its queue;
  - $0 on the billing report.
