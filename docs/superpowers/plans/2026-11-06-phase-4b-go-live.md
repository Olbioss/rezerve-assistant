# Phase 4b — Go-Live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the assistant on the internet: on Cloud Run, deployed by CI, with Rezerve telling it when a kapora is paid and the widget showing the owner's replies. Then switch it on, together, and check every part of it live.

**Architecture:** Three parts.
- **The agent, ready for Cloud Run (Tasks 1–6):**
  - the widget learns when the owner holds a conversation;
  - spans are sent before each Cloud Tasks call back answers;
  - logs are JSON lines;
  - the client address can never be one the client wrote;
  - settings that cannot work stop the service at startup, and `assistant config check` finds them in the production file first;
  - `assistant telegram webhook` points the bot at the service.
- **The deployment (Tasks 7–9):**
  - CI runs the image as the deploy will;
  - `deploy/setup-gcp.sh` sets up the project once;
  - `deploy/push-config.sh` uploads the config;
  - a deploy job pushes each `main` to Cloud Run, keyless, migrating first.
- **Rezerve's side (Tasks 10–11):**
  - the signed `booking.confirmed` event, sent after the payment redirect;
  - the widget polling slowly while the owner holds the conversation.

Then **Go-live**: a runbook you run with me. It covers the accounts, the setup script, the config, the first deploy, Telegram, Vercel's variables, and eleven live checks.

**Tech Stack:**
- Agent: Python 3.13, uv 0.13.1, FastAPI, uvicorn 0.54, pydantic-settings 2.16, SQLAlchemy 2.1 with asyncpg 0.32, Logfire 5.2, pytest.
- Deploy: bash, gcloud 592, GitHub Actions:
  - `google-github-actions/auth@v3`, `google-github-actions/setup-gcloud@v3`;
  - `docker/login-action@v4`, `docker/setup-buildx-action@v4`, `docker/build-push-action@v7`;
  - actionlint 1.7.12, shellcheck.
- Rezerve: Next.js 16.2, React, Vitest 4, Playwright 1.63, Bun 1.3.

**Spec:** `docs/superpowers/specs/2026-10-14-rezerve-assistant-design.md`.
- **Sections:**
  - §5: inbound authentication.
  - §6.4: the widget.
  - §6.5: the kapora event, Rezerve's side.
  - §7.6: the owner relay, as the widget sees it.
  - §7.10: observability.
  - §7.11: configuration.
  - §9: deployment.
  - §11's phase 4 gate.
- **Plan 4a** (`docs/superpowers/plans/2026-11-05-phase-4a-production-service.md`) built the service this plan deploys; its last section lists what it left to this one.
- **Agreed ordering (2026-10-16):** budget caps land before any public Cloud Run URL exists. They did, in 4a.

## Global Constraints

- **Free tiers only, $0 a month.** The code tasks create no cloud resource. Go-live creates them, each inside a free tier, and its last check reads the billing report:
  - Cloud Run, billed per request, 0 to 2 instances;
  - Cloud Tasks;
  - Artifact Registry, 0.5 GB, keeping 3 images;
  - Secret Manager, 6 active versions, 2 kept;
  - Neon Free;
  - Langfuse Hobby.
- **Tests run on local databases only. Never `.env`'s: in Rezerve that is Neon, production.**
  - Agent: `uv run pytest`, on the Compose Postgres (`assistant_test` on 5433), as `tests/conftest.py` defaults.
  - Rezerve unit and integration: `REZERVE_TEST_ENV` below, on the Homebrew Postgres.
  - Rezerve e2e: `REZERVE_E2E_ENV`.

  ```bash
  REZERVE_TEST_ENV="DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_assistant_test BETTER_AUTH_SECRET=ci-not-a-secret-ci-not-a-secret-32 BETTER_AUTH_URL=http://localhost:3000 NEXT_PUBLIC_APP_URL=http://localhost:3000"
  REZERVE_E2E_ENV="DATABASE_URL=postgresql://olbios@localhost:5432/rezerve_e2e BETTER_AUTH_SECRET=ci-not-a-secret-ci-not-a-secret-32 BETTER_AUTH_URL=http://localhost:3000 NEXT_PUBLIC_APP_URL=http://localhost:3000 EMAIL_DELIVERY=off"
  ```

  Use them as `env $REZERVE_TEST_ENV bun run test`. Rezerve's tests need `NEXT_PUBLIC_APP_URL` and Better Auth's two values, as its CI gives them. Without them, 3 tests fail before anything changes.
- **Never read or print a secret.**
  - `assistant config check` names a problem by its key, never its value.
  - `assistant telegram webhook` never prints the bot token.
  - The JSON logs keep httpx below INFO, since Telegram's request URLs carry the token.
  - The go-live config, `deploy/config.json`, is git-ignored.
- **Production is yours to touch.** I write no production data and call no production API. These are blocked for me, and are your commands in Go-live:
  - a push to `main`, of either repository;
  - every gcloud command against the real project;
  - Vercel's variables;
  - `assistant owners link` on the production database.
- **Every agent task ends with all four checks clean,** each output read. Never judge one by a piped `tail`: a pipe hides the exit code.
  - `uv run pytest -q`
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run pyright`
- **Every Rezerve task ends with three checks clean:** `bun run typecheck`, `bun run check` (Biome has one warning already, `noImportantStyles` in `app/globals.css`; no new one), and `env $REZERVE_TEST_ENV bun run test`. Task 11 also builds and runs the e2e suite.
- **The dependency pin stays.** `[tool.uv] exclude-newer = "2026-10-15T00:00:00Z"`. No task adds a Python or npm dependency.
- **Shell scripts run on macOS's bash 3.2,** where you run them, and on CI's bash 5. So no associative arrays, `mapfile` or `${var,,}`.
- **Commit trailer:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Branches:**
  - agent: `feat/go-live`, from `main` at 6386f94. This plan is its first commit.
  - Rezerve: `feat/assistant-go-live`, from `main` at 6d0f675.

## Review Focus

These are the inputs the spec implies, but no test exercised until now, most likely to bite a person first:

1. **A database URL pasted from Neon's console.** It reads `postgresql://…?sslmode=require&channel_binding=require`. asyncpg takes neither parameter, so every connection fails: the migration job first, then each turn. Neon's `-pooler` host adds the statement-cache problem plan 4a ruled out. Expected: the service refuses it at startup, and `assistant config check` names it first. Test: Task 5, `test_the_database_url_is_one_asyncpg_opens`.
2. **A `PUBLIC_BASE_URL` that is not this service's own,** say copied from a dry run or another project. Cloud Tasks mints each call back's token for that audience, so every turn is refused, and no customer ever gets a reply. Expected: the config is never uploaded. Tests: Task 5, `test_the_values_the_deploy_set_up_are_checked`, and Task 8, `test_a_config_with_problems_is_never_uploaded`.
3. **The owner answering after the widget's minute.** Before this plan, the widget stopped polling a minute after the visitor's message. An owner's reply ten minutes later showed only after a page reload. Expected: it keeps looking every ten seconds, and the visitor can write meanwhile. Tests: Task 11, `nextPoll`'s tests and the e2e test `while the owner holds the conversation, the widget looks every ten seconds and the visitor can write`.
4. **The bot token in the logs once INFO is on.** Configuring logging at INFO turns on httpx's request lines, and Telegram's URLs hold the token. 4a's `setLevel` in `telegram.py` worked only because nothing configured logging. Expected: never a URL in the logs. Test: Task 3, `test_the_service_logs_from_info_up_and_no_request_urls`.
5. **`setup-gcp.sh` run a second time,** after a failure halfway, or months later. Expected: nothing is created twice, and what may have drifted is applied again: the queue rates, the cleanup policy, the grants, the GitHub condition. Test: Task 8, `test_a_second_run_creates_nothing_and_applies_the_rest_again`.

## Verified before this plan was written

Every code block below ran in prototypes on 5–6 November 2026. The prototypes were clones of the agent's `main` at 6386f94 and Rezerve's at 6d0f675. Each task was applied in order and committed separately, and each task's counts below are that commit's.
- **The agent:** 514 tests became 556, with pyright strict and ruff clean.
- **Rezerve:**
  - unit and integration, 426 tests to 441;
  - e2e, 18 to 20;
  - typecheck and Biome clean.
- **Every red step was seen red.** Task 11's e2e tests ran against a build of the old widget.
- **The image:**
  - it builds;
  - it migrates an empty database with `alembic upgrade head`;
  - it serves `/health` on `$PORT`, logging JSON;
  - it refuses to start without a model key.
- **Each script test was checked against its script:** dropping a grant from `setup-gcp.sh`, or the check from `push-config.sh`, fails a test. The same goes for the log config: an httpx line at INFO, or uvicorn's access line, fails its test.
- **gcloud, offline.** All 32 gcloud commands the scripts and the workflow run parse under gcloud 592.0.0. gcloud refuses a misspelled flag before it looks for credentials, and none was refused.
- **Linters:**
  - shellcheck 0.11.0 and actionlint 1.7.12 are clean;
  - actionlint runs shellcheck on each step's script, and caught a planted bug;
  - both scripts parse under macOS's bash 3.2.
- **Facts the code rests on:**
  - SQLAlchemy 2.1.4 with asyncpg 0.32: `sslmode=require` raises `TypeError: connect() got an unexpected keyword argument 'sslmode'`, and `ssl=require` is accepted.
  - pydantic-settings 2.16: `Settings.model_validate` still reads the environment, so `from_file` builds a settings class whose only source is the file.
  - uvicorn 0.54: `Config(log_config=dict, access_log=False)` applies the dict, and silences `uvicorn.access`.
  - The HMAC test vector is the same from Python's `assistant.events.signature` and Node's `createHmac`.
- **Checked against live documentation:**
  - Cloud Run's URL `https://SERVICE-PROJECT_NUMBER.REGION.run.app`, its free tier, and that a secret mounted as a file is read at startup.
  - Cloud Tasks' OIDC grants: the enqueuer needs `actAs` on the token's account, and the Cloud Tasks service agent needs Service Account User on it.
  - Workload Identity Federation, as `google-github-actions/auth`'s README sets it up.
  - Artifact Registry's cleanup-policy format.
  - Telegram's `setWebhook` and `getWebhookInfo`.
  - Next.js's `after()`.
  - Neon's, Langfuse's and Secret Manager's free tiers.

**Not checkable before go-live.** Each is one of Go-live's checks, with what to do if it fails:
- **Cloud Run passing `Authorization` untouched.** Its documentation says the signature is stripped from `X-Serverless-Authorization`, and says nothing about `Authorization` on a public service.
- **How many `X-Forwarded-For` entries Cloud Run appends.**
- **Whether budget creation wants a quota project.**
- **Langfuse's ingestion of the service's spans.**

**How to read the code blocks.** A whole file is written as shown. A `diff` block is the change to make, by hand or with `git apply`. Its context lines are exactly what the file holds at that point when the tasks run in order: the blocks were generated from the prototype's commits, and the plan was replayed from them on fresh clones.

## Rulings made while prototyping

Each comes with what it costs if wrong.

- **`assistant telegram webhook`, not spec §9's `scripts/set_webhook.py`.** It reads the same production file as `assistant config check`, and sits with `assistant owners link`. If wrong, it costs a rename.
- **The direct Neon endpoint, required.** Plan 4a's reason: the pooled one needs asyncpg's statement cache off. Two instances need no pooler. If wrong, it costs a validator line and `statement_cache_size=0`.
- **The widget's requests are not traced at all** (`/web/`). It polls every second while an answer comes, and now every ten seconds while the owner holds a conversation. Those would spend Langfuse's 50k units a month on nothing. This also closes 4a's deferred minor on Langfuse units. If wrong, a failing widget request shows in Cloud Run's request log, not in Langfuse.
- **Each `/internal` call back flushes its spans before it answers.** On a CPU billed per request, the exporter's own timer may never run. If wrong, it costs one Langfuse round trip per call back, which no customer waits on.
- **Logs are JSON lines on stdout, and uvicorn's access log is off.** Cloud Run logs every request itself. If wrong, it costs `access_log=True`.
- **`python -m assistant.serve` replaces the uvicorn command line,** so the log config is one Python dict and tested. If wrong, nothing is lost: `uvicorn --factory` still works for development.
- **The client address logs once per instance,** as a count of `X-Forwarded-For` entries, never an address. That is how `TRUSTED_PROXY_HOPS` gets settled live. If wrong, it costs one log line per instance.
- **`TRUSTED_PROXY_HOPS` must be at least 1.** With 0, `forwarded[-0]` is the leftmost entry, which the client writes, so each post could name a new address. This was 4a's deferred minor. If wrong, there is no cost: 0 was never safe.
- **A bot token needs its webhook secret,** and Cloud Tasks needs an https `PUBLIC_BASE_URL`. Both checks are at startup, closing 4a's deferred "no startup checks on the config". If wrong, each costs a validator.
- **`deploy/config.example.json` marks each value still to fill with `REPLACE_ME`,** and the check refuses a file that still holds one. If wrong, it costs a constant.
- **Each revision pins its config version.** A secret mounted as `latest` would give instances started at different times different configs. `push-config.sh` moves the running service to the new version, and keeps the one before it for a rollback. If wrong, a rollback older than one version needs that config pushed again.
- **The tasks account gets no `run.invoker`.** The service is public, and `/internal` checks the token itself, as 4a built it. If wrong, it costs one binding.
- **The budget is 1 in the billing account's currency,** not spec §9's $1. A budget must use the account's currency. In lira it alerts sooner, which costs nothing. If wrong, it costs a flag.
- **GitHub may deploy from this repository's `main` only,** by name and ref. If wrong, a deleted and recreated `Olbioss/rezerve-assistant` could deploy. Only you can create that name.
- **The deploy job lives in `ci.yml`.** It needs the test, image and workflow jobs, and is skipped until `vars.GCP_PROJECT` is set. "Run workflow" on `main` deploys with no new commit. If wrong, it costs moving it to a file of its own.
- **The widget polls slowly while the owner holds the conversation:**
  - every 10 s;
  - for 30 minutes after the last news;
  - only while the chat is open and the tab in view.

  Opening the chat again, or coming back to the tab, fetches at once, which closes 4a's deferred "polling not restarting on reopen". If wrong, an owner's reply after 30 quiet minutes shows at the visitor's next open or return.
- **Rezerve sends the event with `after()`, once, with a 10 s timeout.** The customer's redirect never waits on a cold start. If wrong, a cold start over 10 s loses the chat notice. The confirmation page and email still tell the customer.
- **Telegram sends only `message` updates.** An edited message was never read. If wrong, it costs a list entry.
- **CI starts the image with a dummy `GEMINI_API_KEY`.** The service refuses to start with no model key at all. If wrong, there is no cost: no model is called.

## File structure

**Agent, created:**

| File | Responsibility |
|---|---|
| `src/assistant/logs.py` | JSON log lines Cloud Logging reads, and the log config |
| `src/assistant/serve.py` | The container's command: uvicorn on `$PORT`, with that config |
| `src/assistant/config_check.py` | `assistant config check`: a production file's problems, by key |
| `deploy/config.example.json` | Every production setting, `REPLACE_ME` where a value goes |
| `deploy/setup-gcp.sh` | The project, once; safe to run again |
| `deploy/push-config.sh` | The config: checked, uploaded, the service moved to it |
| `deploy/cleanup-policy.json` | Artifact Registry keeps the last 3 images |
| `tests/fakes/gcloud.py` | gcloud stood in for on PATH |
| `tests/unit/test_logs.py`, `test_config_check.py`, `test_cli_telegram.py`, `test_setup_gcp.py`, `test_push_config.py`, `test_event_signature.py` | Their tests |

**Agent, modified:**
- the store, the web channel and observability;
- the services bundle and the app factory;
- the internal endpoints;
- config, the CLI, the Telegram client;
- the rate limiter;
- the Dockerfile, Compose, CI, `.gitignore`, the README;
- and their tests, with the fake Telegram.

**Rezerve:**

| File | Change |
|---|---|
| `lib/assistant/events.ts` | Created: the event's type, its signature, its sending |
| `lib/payments/handle-payment-result.ts` | `confirmPaidBooking` tells the assistant, after the response |
| `lib/assistant/chat-client.ts` | `with_owner`, `nextPoll` and its two waits |
| `components/assistant/chat-widget.tsx` | Polls as `nextPoll` says; again on reopen and on return to the tab |
| `README.md`, and the tests beside each file, and `e2e/assistant-widget.spec.ts` | |

## Setup

- [ ] **Step 1: The agent's branch, with this plan as its first commit**

```bash
cd ~/repos/deploys/rezerve-assistant
git switch main && git pull --ff-only
git switch -c feat/go-live
git add docs/superpowers/plans/2026-11-06-phase-4b-go-live.md
git commit -m "docs: plan 4b, go-live

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Rezerve's branch**

```bash
cd ~/repos/deploys/rezerve
git switch main && git pull --ff-only
git switch -c feat/assistant-go-live
```

- [ ] **Step 3: The baselines, all green before anything changes**

```bash
cd ~/repos/deploys/rezerve-assistant
docker compose up -d db
uv run pytest -q          # 514 passed
cd ~/repos/deploys/rezerve
env $REZERVE_TEST_ENV bun run db:migrate
env $REZERVE_TEST_ENV bun run test   # 426 passed
```

Expected: 514 and 426 passed. If Rezerve fails 3 tests on `NEXT_PUBLIC_APP_URL`, the environment above is missing.

---

### Task 1: The widget is told while the owner holds the conversation

Spec §7.6: once a conversation is handed off, the business owner answers when they can, from Telegram. The widget polls only while `pending`, a minute at most, so an owner's reply shows only after a reload. That is 4a's open item I5. The agent's half of the fix: each message page says `with_owner`, true while the conversation's state is `human`. Rezerve's half is Task 11.

**Files:**
- Modify: `src/assistant/conversation/store.py`, `src/assistant/channels/web.py`
- Test: `tests/integration/test_store.py`, `tests/integration/test_web.py`

**Interfaces:**
- Consumes: `Store.messages_after(business_slug, channel, channel_user_id, after) -> MessagesPage`, `Store.commit_turn`, `Store.hand_back`, `TurnResult(state=...)` (all 4a).
- Produces: `MessagesPage.with_owner: bool`, False when there is no open conversation. `GET /web/{slug}/messages` answers `{"messages": [...], "pending": bool, "with_owner": bool}`. Task 11 reads it.

- [ ] **Step 1: Write the failing tests**

Modify `tests/integration/test_store.py`:

```diff
--- a/tests/integration/test_store.py
+++ b/tests/integration/test_store.py
@@ -110,6 +110,22 @@ async def test_the_page_shows_a_reply_once_it_is_sent(store: Store) -> None:
     assert after.messages == []
 
 
+async def test_the_page_says_while_the_owner_holds_the_conversation(store: Store) -> None:
+    # Spec §7.6: the owner answers when they can, long after the widget has
+    # stopped waiting for a reply. The page tells it to keep looking.
+    recorded = await store.record_inbound(inbound(), REF, NOW)
+    assert recorded.seq is not None
+    handed = TurnResult(processed=[recorded.seq], replies=[], state="human")
+    await store.commit_turn(recorded.conversation_id, handed, NOW)
+    page = await store.messages_after("demo", "web", "visitor-1", 0)
+    assert page.with_owner
+    assert not page.pending
+
+    await store.hand_back(recorded.conversation_id, "Asistan yeniden sizinle.")
+    page = await store.messages_after("demo", "web", "visitor-1", 0)
+    assert not page.with_owner
+
+
 async def test_a_turn_commits_history_approval_and_run_together(store: Store) -> None:
     recorded = await store.record_inbound(inbound("randevu"), REF, NOW)
     assert recorded.seq is not None
```

Modify `tests/integration/test_web.py`:

```diff
--- a/tests/integration/test_web.py
+++ b/tests/integration/test_web.py
@@ -2,6 +2,7 @@
 its lifespan, the local queue, Postgres, the fake Rezerve, a scripted model."""
 
 from collections.abc import AsyncIterator
+from datetime import UTC, datetime
 from typing import Any, cast
 from uuid import uuid4
 
@@ -10,7 +11,8 @@ import pytest
 from fastapi import FastAPI
 
 from assistant.config import Settings
-from assistant.conversation.store import Store
+from assistant.conversation.store import Store, TurnResult
+from assistant.identity import customer_ref
 from assistant.main import create_app
 from assistant.queue.local import LocalTaskQueue
 from assistant.services import AppServices
@@ -101,6 +103,7 @@ async def test_a_message_is_answered_and_polled_back(widget: Widget, script: Scr
     assert await widget.poll(after=page["messages"][-1]["seq"]) == {
         "messages": [],
         "pending": False,
+        "with_owner": False,
     }
 
 
@@ -120,7 +123,25 @@ async def test_a_visitor_sees_only_their_own_conversation(widget: Widget, script
     await widget.post("merhaba")
     await widget.settle()
     widget.visitor_id = str(uuid4())
-    assert await widget.poll() == {"messages": [], "pending": False}
+    assert await widget.poll() == {"messages": [], "pending": False, "with_owner": False}
+
+
+async def test_the_widget_keeps_looking_while_the_owner_holds_the_conversation(
+    widget: Widget, script: Script, store: Store
+) -> None:
+    # The owner's reply can come long after the widget's 60 s of waiting for
+    # one (spec §7.6); with_owner tells it to keep polling, slowly.
+    script.add(says("Merhaba!"))
+    await widget.post("merhaba")
+    await widget.settle()
+    assert (await widget.poll())["with_owner"] is False
+
+    ref = customer_ref("s", "web", widget.visitor_id)
+    conversation_id = await store.latest_conversation("demo", ref)
+    assert conversation_id is not None
+    handed = TurnResult(processed=[], replies=[], state="human")
+    await store.commit_turn(conversation_id, handed, datetime.now(UTC))
+    assert (await widget.poll())["with_owner"] is True
 
 
 @pytest.mark.parametrize(
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/integration/test_store.py tests/integration/test_web.py`
Expected: 4 failed. `test_store`'s new test fails with `AttributeError` (`MessagesPage` has no `with_owner`), two exact-match assertions in `test_web` lack the key, and the new web test fails with `KeyError: 'with_owner'`.

- [ ] **Step 3: Implement**

Modify `src/assistant/conversation/store.py`:

```diff
--- a/src/assistant/conversation/store.py
+++ b/src/assistant/conversation/store.py
@@ -151,6 +151,9 @@ class MessagesPage:
     messages: list[PageMessage]
     # True while something is still to come: a message not yet answered.
     pending: bool
+    # True while the business owner holds the conversation (spec §7.6): their
+    # replies come when they come, so the widget keeps looking.
+    with_owner: bool
 
 
 class Store:
@@ -769,18 +772,19 @@ class Store:
     ) -> MessagesPage:
         """The open conversation's messages after `after`, as its customer sees them."""
         async with self._sessions() as session:
-            conversation_id = (
+            found = (
                 await session.execute(
-                    sa.select(Conversation.id).where(
+                    sa.select(Conversation.id, Conversation.state).where(
                         Conversation.business_slug == business_slug,
                         Conversation.channel == channel,
                         Conversation.channel_user_id == channel_user_id,
                         Conversation.state != "closed",
                     )
                 )
-            ).scalar_one_or_none()
-            if conversation_id is None:
-                return MessagesPage(messages=[], pending=False)
+            ).one_or_none()
+            if found is None:
+                return MessagesPage(messages=[], pending=False, with_owner=False)
+            conversation_id, state = found
             # The page stops short of the first reply not yet sent: a reader's
             # cursor that moved past it would never see it once it was.
             # min() over no rows is NULL, which the typed select cannot say.
@@ -834,6 +838,7 @@ class Store:
                     for row in rows.scalars()
                 ],
                 pending=pending,
+                with_owner=state == "human",
             )
 
 
```

Modify `src/assistant/channels/web.py`:

```diff
--- a/src/assistant/channels/web.py
+++ b/src/assistant/channels/web.py
@@ -4,7 +4,8 @@ The widget keeps a random UUID in localStorage as its visitor id. That
 unguessable id is the credential — the same model as a booking's id on its
 page — so these endpoints take no other, and it never travels in a URL: URLs
 end up in access logs. The widget posts a message (the id in the body), then
-polls for the answer while `pending` is true (the id in `X-Visitor-Id`).
+polls for the answer while `pending` is true (the id in `X-Visitor-Id`), and
+slowly while `with_owner` is: the business owner answers when they can.
 """
 
 from datetime import UTC, datetime
@@ -55,6 +56,8 @@ class MessageOut(BaseModel):
 class MessagesOut(BaseModel):
     messages: list[MessageOut]
     pending: bool
+    # The owner holds the conversation: their reply may come at any time.
+    with_owner: bool
 
 
 class WebChannel:
@@ -108,4 +111,5 @@ async def get_messages(
             for message in page.messages
         ],
         pending=page.pending,
+        with_owner=page.with_owner,
     )
```

- [ ] **Step 4: Run the four checks**

Expected: 516 passed; ruff and pyright clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(web): the widget is told while the owner holds the conversation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Spans sent before a call back answers; the widget not traced

Cloud Run with request-based billing (spec §9) gives an instance CPU only while it answers a request. The `BatchSpanProcessor` exports on its own timer, so spans finished in a turn may sit until the next request, or go with the instance. Each `/internal` endpoint now flushes in a `finally`, before it answers. The flush runs `logfire.force_flush` off the event loop, and is nothing when tracing is off.

The widget's requests leave tracing altogether. `/web/` joins `/health` in `excluded_urls`, which covers the polls, the posts and the CORS preflights. One poll a second while an answer comes, and one every ten seconds while the owner holds a conversation (Task 11), would spend Langfuse's 50k units a month on nothing. That was 4a's deferred minor on the widget's polls.

**Files:**
- Modify: `src/assistant/observability.py`, `src/assistant/services.py`, `src/assistant/main.py`, `src/assistant/internal.py`
- Test: `tests/unit/test_observability.py`, `tests/unit/test_traces_exported.py`, `tests/trace_probe.py`, `tests/integration/test_internal.py`

**Interfaces:**
- Consumes: `observability.configure(settings, app)`, `create_app(...)`, `AppServices` (4a).
- Produces:
  - `observability.UNTRACED = "/health,/web/"`;
  - `type observability.Flush = Callable[[], Awaitable[None]]`;
  - `observability.flusher(settings) -> Flush`;
  - `AppServices.flush_traces`;
  - `create_app(..., flush_traces: observability.Flush | None = None)`, which tests pass.

- [ ] **Step 1: Write the failing tests**

The probe, `tests/trace_probe.py`, runs the real Logfire pipeline in a fresh process. It now also sends the widget's preflight and post, and flushes the way `/internal` does.

Modify `tests/unit/test_observability.py`:

```diff
--- a/tests/unit/test_observability.py
+++ b/tests/unit/test_observability.py
@@ -73,4 +73,28 @@ def test_traces_go_to_the_endpoint_and_nowhere_else(monkeypatch: pytest.MonkeyPa
     # Which arguments reach the request spans: tests/unit/test_traces_exported.py.
     (instrumented,), options = seen["instrument_fastapi"]
     assert instrumented is app
-    assert options["excluded_urls"] == "/health"
+    # The widget's polls would spend Langfuse's units: tests/unit/test_traces_exported.py.
+    assert options["excluded_urls"] == "/health,/web/"
+
+
+async def test_without_an_endpoint_a_flush_is_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
+    def refuse(*args: Any, **kwargs: Any) -> bool:
+        raise AssertionError("logfire was flushed")
+
+    monkeypatch.setattr(observability.logfire, "force_flush", refuse)
+    await observability.flusher(settings())()
+
+
+async def test_with_an_endpoint_a_flush_sends_the_finished_spans(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    flushed: list[str] = []
+
+    def force_flush(*args: Any, **kwargs: Any) -> bool:
+        flushed.append("now")
+        return True
+
+    monkeypatch.setattr(observability.logfire, "force_flush", force_flush)
+    endpoint = "https://cloud.langfuse.com/api/public/otel/v1/traces"
+    await observability.flusher(settings(otel_traces_endpoint=endpoint))()
+    assert flushed == ["now"]
```

Modify `tests/unit/test_traces_exported.py`:

```diff
--- a/tests/unit/test_traces_exported.py
+++ b/tests/unit/test_traces_exported.py
@@ -45,12 +45,20 @@ def test_a_request_trace_holds_no_secret_and_nothing_a_customer_wrote(
     # Logfire records each endpoint's arguments by default: the settings (the
     # database URL among them), the update a customer sent, the visitor id.
     assert any("/telegram/webhook" in span["name"] for span in exported)
-    assert any("/web/{slug}/messages" in span["name"] for span in exported)
     sent = json.dumps(exported)
     private = (trace_probe.DATABASE_PASSWORD, trace_probe.CUSTOMER_TEXT, trace_probe.VISITOR_ID)
     assert [value for value in private if value in sent] == []
 
 
+def test_the_widgets_requests_leave_no_trace(exported: list[dict[str, Any]]) -> None:
+    # The widget polls every second while a reply is coming, and every ten
+    # seconds while the owner holds the conversation. As traces, its requests
+    # would spend the 50k units a month Langfuse's free plan has for the turns.
+    names = [span["name"] for span in exported]
+    assert any("/telegram/webhook" in name for name in names)
+    assert [name for name in names if "/web/" in name] == []
+
+
 def test_the_conversation_reaches_langfuse_as_the_traces_session(
     exported: list[dict[str, Any]],
 ) -> None:
```

Modify `tests/trace_probe.py`:

```diff
--- a/tests/trace_probe.py
+++ b/tests/trace_probe.py
@@ -14,7 +14,6 @@ from typing import Any
 from unittest.mock import patch
 
 import httpx
-import logfire
 from opentelemetry.sdk.trace import ReadableSpan
 from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
 from pydantic_ai.models.test import TestModel
@@ -47,7 +46,8 @@ class KeptExporter(SpanExporter):
 
 
 async def requests(settings: Settings) -> None:
-    """A customer's Telegram message and a widget poll, as they arrive."""
+    """A customer's Telegram message, and the widget's preflight, post and poll,
+    as they arrive."""
     app = create_app(settings, model=TestModel())
     async with app.router.lifespan_context(app):
         # Both fail at the database, once their arguments are read.
@@ -67,6 +67,18 @@ async def requests(settings: Settings) -> None:
                 json=update,
                 headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
             )
+            preflight = {
+                "Origin": "http://localhost:3000",
+                "Access-Control-Request-Method": "POST",
+                "Access-Control-Request-Headers": "content-type",
+            }
+            await client.options("/web/demo/messages", headers=preflight)
+            posted = {
+                "visitor_id": VISITOR_ID,
+                "client_message_id": "5b0a3f0e-4c4a-4d8e-9d55-2f5f4e3b1c20",
+                "text": CUSTOMER_TEXT,
+            }
+            await client.post("/web/demo/messages", json=posted)
             await client.get("/web/demo/messages", headers={"X-Visitor-Id": VISITOR_ID})
 
 
@@ -81,6 +93,11 @@ def turn() -> None:
             pass
 
 
+async def flush(settings: Settings) -> None:
+    """The service's own flush, as /internal calls run it before answering."""
+    await observability.flusher(settings)()
+
+
 def main() -> None:
     settings = Settings(
         _env_file=None,  # pyright: ignore[reportCallIssue]
@@ -95,7 +112,7 @@ def main() -> None:
     with patch.object(observability, "OTLPSpanExporter", KeptExporter):
         asyncio.run(requests(settings))
     turn()
-    logfire.force_flush()
+    asyncio.run(flush(settings))
     spans = [{"name": span.name, "attributes": dict(span.attributes or {})} for span in exported]
     print(json.dumps(spans, default=str, ensure_ascii=False))
 
```

Modify `tests/integration/test_internal.py`:

```diff
--- a/tests/integration/test_internal.py
+++ b/tests/integration/test_internal.py
@@ -30,11 +30,24 @@ async def verify(token: str, audience: str) -> Mapping[str, Any]:
     return {"email": TOKENS[token], "email_verified": True, "aud": audience}
 
 
+class Flushes:
+    """Stands in for sending the finished spans: counts each time it is asked."""
+
+    def __init__(self) -> None:
+        self.count = 0
+
+    async def __call__(self) -> None:
+        self.count += 1
+
+
 class Service:
-    def __init__(self, client: httpx.AsyncClient, tasks: FakeTasks, script: Script) -> None:
+    def __init__(
+        self, client: httpx.AsyncClient, tasks: FakeTasks, script: Script, flushes: Flushes
+    ) -> None:
         self.client = client
         self.tasks = tasks
         self.script = script
+        self.flushes = flushes
         self.visitor_id = str(uuid4())
 
     async def post(self, text: str) -> None:
@@ -73,17 +86,19 @@ async def service(database_url: str, store: Store) -> AsyncIterator[Service]:
         gcp_project="rezerve-assistant",
         tasks_invoker_sa=INVOKER,
     )
+    flushes = Flushes()
     app = create_app(
         settings,
         model=script.model,
         open_rezerve=lambda slug, ref: fake.session(),
         tasks_client=tasks,
         verify_token=verify,
+        flush_traces=flushes,
     )
     async with app.router.lifespan_context(app):
         transport = httpx.ASGITransport(app=app)
         async with httpx.AsyncClient(transport=transport, base_url="http://assistant") as client:
-            yield Service(client, tasks, script)
+            yield Service(client, tasks, script, flushes)
 
 
 async def test_a_message_queues_a_task_whose_call_back_answers_it(service: Service) -> None:
@@ -99,6 +114,16 @@ async def test_a_message_queues_a_task_whose_call_back_answers_it(service: Servi
     assert await service.replies() == ["Merhaba! Size nasıl yardımcı olabilirim?"]
 
 
+async def test_a_call_back_sends_its_traces_before_it_answers(service: Service) -> None:
+    # Cloud Run gives an instance CPU only while it answers a request: spans
+    # left to the exporter's own timer would wait for the next request, or go
+    # with the instance.
+    service.script.add(says("Merhaba!"))
+    await service.post("merhaba")
+    assert (await service.call_back(service.last_task_path())).status_code == 204
+    assert service.flushes.count == 1
+
+
 @pytest.mark.parametrize(
     ("authorization", "status"),
     [(None, 401), ("forged", 401), ("stranger-token", 403)],
@@ -124,6 +149,7 @@ async def test_a_turn_another_handler_holds_is_tried_again_later(
 
     # 429: Cloud Tasks retries it with backoff (spec §7.2).
     assert response.status_code == 429
+    assert service.flushes.count == 1
 
 
 async def test_a_reminder_task_calls_back_with_the_invokers_token(service: Service) -> None:
@@ -134,3 +160,5 @@ async def test_a_reminder_task_calls_back_with_the_invokers_token(service: Servi
         "/internal/reminder", json=body, headers={"Authorization": "Bearer invoker-token"}
     )
     assert response.status_code == 204
+    # The refused call never reached the endpoint: only the reminder flushed.
+    assert service.flushes.count == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/unit/test_observability.py tests/unit/test_traces_exported.py tests/integration/test_internal.py`
Expected: 3 failed, 10 errors. `observability` has no `flusher`, and `create_app` takes no `flush_traces`.

- [ ] **Step 3: Implement**

Modify `src/assistant/observability.py`:

```diff
--- a/src/assistant/observability.py
+++ b/src/assistant/observability.py
@@ -6,9 +6,14 @@ the bot token. What customers wrote is left out unless TRACE_CONTENT is set,
 since it is personal data and Langfuse keeps it for 30 days. And no request
 span carries its endpoint's arguments: they hold the settings, the database
 URL among them, what a customer wrote, and the widget's visitor ids.
+
+The widget's requests are not traced at all. It polls every second while a
+reply is coming, and every ten seconds while the owner holds a conversation:
+those would spend the 50k units a month Langfuse's free plan has for turns.
 """
 
-from collections.abc import Generator
+import asyncio
+from collections.abc import Awaitable, Callable, Generator
 from contextlib import contextmanager
 from typing import Any
 from urllib.parse import unquote
@@ -25,6 +30,11 @@ SERVICE = "rezerve-assistant"
 # What Langfuse groups traces by. Logfire's scrubber would redact the session
 # for its name, but here it names a conversation, not a login.
 LANGFUSE_KEYS = frozenset({"langfuse.session.id", "langfuse.user.id"})
+# Requests left out of traces: regular expressions, searched in each URL.
+UNTRACED = "/health,/web/"
+
+# Sends the spans finished so far, before a request answers.
+type Flush = Callable[[], Awaitable[None]]
 
 
 def otlp_headers(raw: str) -> dict[str, str]:
@@ -53,12 +63,28 @@ def configure(settings: Settings, app: FastAPI) -> bool:
         scrubbing=logfire.ScrubbingOptions(callback=_keep_langfuse_keys),
     )
     logfire.instrument_pydantic_ai(include_content=settings.trace_content)
-    logfire.instrument_fastapi(
-        app, excluded_urls="/health", request_attributes_mapper=_no_arguments
-    )
+    logfire.instrument_fastapi(app, excluded_urls=UNTRACED, request_attributes_mapper=_no_arguments)
     return True
 
 
+def flusher(settings: Settings) -> Flush:
+    """What sends a request's finished spans before it answers. Cloud Run
+    gives an instance CPU only while it answers a request: spans left to the
+    exporter's own timer wait for the next request, or go with the instance."""
+    if settings.otel_traces_endpoint is None:
+        return _nothing
+    return _flush
+
+
+async def _flush() -> None:
+    # It waits on Langfuse's answer: off the event loop.
+    await asyncio.to_thread(logfire.force_flush)
+
+
+async def _nothing() -> None:
+    return None
+
+
 def _keep_langfuse_keys(match: logfire.ScrubMatch) -> Any:
     """Everything the scrubber finds is redacted, except Langfuse's own keys."""
     return match.value if match.path[-1] in LANGFUSE_KEYS else None
```

Modify `src/assistant/services.py`:

```diff
--- a/src/assistant/services.py
+++ b/src/assistant/services.py
@@ -31,6 +31,8 @@ class AppServices:
     telegram: TelegramApi | None
     # Sends a booking's reminder: what a reminder task runs.
     send_reminder: Callable[[str, datetime], Awaitable[None]]
+    # Sends the spans finished so far; /internal calls run it before answering.
+    flush_traces: Callable[[], Awaitable[None]]
 
 
 def app_services(request: Request) -> AppServices:
```

Modify `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -37,9 +37,11 @@ def create_app(
     tasks_client: TaskCreator | None = None,
     verify_token: TokenVerifier | None = None,
     telegram_transport: httpx.AsyncBaseTransport | None = None,
+    flush_traces: observability.Flush | None = None,
 ) -> FastAPI:
-    """Tests pass a model, a Rezerve, Cloud Tasks, a token check and Telegram's
-    transport of their own; production builds each from settings."""
+    """Tests pass a model, a Rezerve, Cloud Tasks, a token check, Telegram's
+    transport and a trace flush of their own; production builds each from
+    settings."""
     config = settings or Settings()  # pyright: ignore[reportCallIssue]
 
     @asynccontextmanager
@@ -106,6 +108,7 @@ def create_app(
                 verify_token=verify_token or internal.verify_google_token,
                 telegram=telegram,
                 send_reminder=reminders.send,
+                flush_traces=flush_traces or observability.flusher(config),
             )
             yield
 
```

Modify `src/assistant/internal.py`:

```diff
--- a/src/assistant/internal.py
+++ b/src/assistant/internal.py
@@ -76,6 +76,9 @@ async def turn(conversation_id: int, services: Services) -> Response:
     except LeaseBusy:
         # Another handler has the conversation: Cloud Tasks tries again later.
         raise HTTPException(status_code=429, detail="busy") from None
+    finally:
+        # While this request lasts, the instance has the CPU to send them.
+        await services.flush_traces()
     return Response(status_code=204)
 
 
@@ -86,5 +89,8 @@ class DueReminder(BaseModel):
 
 @router.post("/reminder", status_code=204, dependencies=[Depends(from_cloud_tasks)])
 async def reminder(due: DueReminder, services: Services) -> Response:
-    await services.send_reminder(due.booking_id, due.starts_at)
+    try:
+        await services.send_reminder(due.booking_id, due.starts_at)
+    finally:
+        await services.flush_traces()
     return Response(status_code=204)
```

- [ ] **Step 4: Run the four checks, and look at the probe's spans**

```bash
env -i HOME="$HOME" PATH="$PATH" PYDANTIC_AI_NO_BANNER=1 uv run python -m tests.trace_probe 2>/dev/null \
  | python3 -c 'import json,sys; print([s["name"] for s in json.load(sys.stdin)])'
```

Expected: `['POST /telegram/webhook', 'model call', 'turn']`, with no `/web/` span. Then 520 passed; ruff and pyright clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(traces): a call back sends its spans before it answers; the widget is not traced

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Log lines Cloud Logging reads

Cloud Run sends what a container writes to Cloud Logging. A line holding one JSON object becomes an entry with that object's `severity`. Nothing configures logging today. So Python drops the service's INFO lines, such as a reminder skipped or a task already queued, and prints its warnings bare, with no severity.

The container's command becomes `python -m assistant.serve`. It runs uvicorn on `$PORT` with one dict config:
- INFO and up, on stdout, one JSON object a line;
- a traceback kept in the message, where Error Reporting looks;
- uvicorn's access lines off, since Cloud Run logs every request itself;
- httpx and httpcore at WARNING. At INFO, httpx logs each request's URL, and Telegram's hold the bot token.

**Files:**
- Create: `src/assistant/logs.py`, `src/assistant/serve.py`
- Modify: `Dockerfile`, `compose.yaml`, `src/assistant/main.py` (its docstring)
- Test: `tests/unit/test_logs.py`

**Interfaces:**
- Produces:
  - `logs.CloudFormatter`, a `logging.Formatter`;
  - `logs.CONFIG: dict[str, Any]`, a `dictConfig`;
  - `serve.config() -> uvicorn.Config`;
  - `serve.main()`.
- Task 7 runs the image with it; Task 4's log line reaches Cloud Logging through it.

- [ ] **Step 1: Write the failing tests**

The config is checked in a fresh process: configuring logging inside pytest would change it for every later test.

Create `tests/unit/test_logs.py`:

```python
"""The service's log lines, as Cloud Logging reads them: Cloud Run sends what
a container writes to Cloud Logging, which turns a line holding one JSON
object into an entry with that object's severity."""

import json
import logging
import subprocess
import sys

from assistant import logs

# The container's own logging, in a fresh process: configuring it here would
# change the logging of every test after this one.
SERVED = """
import logging
from assistant import serve

serve.config()
logging.getLogger("assistant.reminders").info("No reminder for booking %s", "b1")
logging.getLogger("assistant.reminders").debug("Not this one")
logging.getLogger("uvicorn.error").info("Application startup complete.")
logging.getLogger("uvicorn.access").info("GET /health 200")
logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/bot123:SECRET/sendMessage")
"""


def test_a_line_is_one_json_object_with_its_severity() -> None:
    record = logging.LogRecord(
        "assistant.test", logging.WARNING, __file__, 1, "Chat %s unreachable", (42,), None
    )
    assert json.loads(logs.CloudFormatter().format(record)) == {
        "severity": "WARNING",
        "message": "Chat 42 unreachable",
        "logger": "assistant.test",
    }


def test_an_error_keeps_its_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "assistant.test", logging.ERROR, __file__, 1, "Turn failed", (), sys.exc_info()
        )
    entry = json.loads(logs.CloudFormatter().format(record))
    assert entry["severity"] == "ERROR"
    # Error Reporting groups errors by the stack trace in the message.
    assert entry["message"].startswith("Turn failed\nTraceback")
    assert "RuntimeError: boom" in entry["message"]


def test_the_service_logs_from_info_up_and_no_request_urls() -> None:
    # Unconfigured, Python drops the service's INFO lines and prints its
    # warnings bare, with no severity for Cloud Logging to read. Cloud Run
    # logs every request itself; httpx's request lines carry the bot token.
    done = subprocess.run(
        [sys.executable, "-c", SERVED], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stderr == ""
    entries = [json.loads(line) for line in done.stdout.splitlines()]
    assert entries == [
        {
            "severity": "INFO",
            "message": "No reminder for booking b1",
            "logger": "assistant.reminders",
        },
        {
            "severity": "INFO",
            "message": "Application startup complete.",
            "logger": "uvicorn.error",
        },
    ]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/unit/test_logs.py`
Expected: a collection error, `ImportError: cannot import name 'logs' from 'assistant'`.

- [ ] **Step 3: Implement**

Create `src/assistant/logs.py`:

```python
"""Log lines Cloud Logging can read: Cloud Run sends what the container
writes to Cloud Logging, which makes a line holding one JSON object an entry
with that object's severity and message."""

import json
import logging
from typing import Any


class CloudFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            # Error Reporting groups errors by the stack trace in the message.
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return json.dumps({"severity": record.levelname, "message": message, "logger": record.name})


# What the service logs, from INFO up, on stdout: uvicorn's lines too.
CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"cloud": {"()": CloudFormatter}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "cloud",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"level": "INFO", "handlers": ["stdout"]},
    "loggers": {
        # httpx logs each request's URL at INFO, and Telegram's carry the bot token.
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
}
```

Create `src/assistant/serve.py`:

```python
"""The container's command, `python -m assistant.serve`: the service under
uvicorn, on the port Cloud Run gives in $PORT (8000 locally)."""

import os

import uvicorn

from assistant import logs


def config() -> uvicorn.Config:
    # Cloud Run logs every request itself: uvicorn's access lines would be
    # each request twice.
    return uvicorn.Config(
        "assistant.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        log_config=logs.CONFIG,
        access_log=False,
    )


def main() -> None:
    uvicorn.Server(config()).run()


if __name__ == "__main__":
    main()
```

Modify `Dockerfile`:

```diff
--- a/Dockerfile
+++ b/Dockerfile
@@ -18,5 +18,5 @@ COPY --from=build /app /app
 ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYDANTIC_AI_NO_BANNER=1
 USER assistant
 EXPOSE 8000
-# Cloud Run says which port in $PORT; locally it is 8000.
-CMD ["sh", "-c", "exec uvicorn assistant.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
+# uvicorn, on Cloud Run's $PORT (8000 locally), logging JSON lines.
+CMD ["python", "-m", "assistant.serve"]
```

Modify `compose.yaml`:

```diff
--- a/compose.yaml
+++ b/compose.yaml
@@ -38,7 +38,7 @@ services:
     command:
       - sh
       - -c
-      - alembic upgrade head && exec uvicorn assistant.main:create_app --factory --host 0.0.0.0 --port 8000
+      - alembic upgrade head && exec python -m assistant.serve
 
 volumes:
   db-data:
```

Modify `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -1,4 +1,4 @@
-"""The service: `uvicorn assistant.main:create_app --factory`."""
+"""The service, which `python -m assistant.serve` runs under uvicorn."""
 
 from collections.abc import AsyncGenerator
 from contextlib import AsyncExitStack, asynccontextmanager
```

- [ ] **Step 4: Run the four checks, then the image**

Expected: 523 passed; ruff and pyright clean. Then the image on Cloud Run's port, against the test database:

```bash
docker build -q -t assistant-logs . && docker run -d --name assistant-logs \
  --add-host host.docker.internal:host-gateway -p 18080:8080 -e PORT=8080 \
  -e DATABASE_URL=postgresql+asyncpg://postgres:postgres@host.docker.internal:5433/assistant_test \
  -e ASSISTANT_MCP_TOKEN=x -e CUSTOMER_REF_SECRET=y -e GEMINI_API_KEY=z assistant-logs
sleep 5; curl -fsS localhost:18080/health; docker logs assistant-logs; docker rm -f assistant-logs
```

Expected: `{"status":"ok"}`, and every log line a JSON object, such as `{"severity": "INFO", "message": "Application startup complete.", "logger": "uvicorn.error"}`. There is no line for the `/health` request.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(logs): JSON lines Cloud Logging reads, from INFO up, under python -m assistant.serve

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: The client address, never one the client wrote

The widget's limit per address counts the `X-Forwarded-For` entry `TRUSTED_PROXY_HOPS` places from the right, where the platform appends. No Cloud Run page says how many entries its front end appends. So each instance logs, once, how many entries a widget post carried, never an address, and Go-live reads the line to settle the setting.

`TRUSTED_PROXY_HOPS=0` was 4a's deferred minor: `forwarded[-0]` is `forwarded[0]`, the leftmost entry, which the client writes. It is now refused.

**Files:**
- Modify: `src/assistant/ratelimit.py`, `src/assistant/config.py`, `src/assistant/services.py`, `src/assistant/main.py`, `src/assistant/channels/web.py`
- Test: `tests/unit/test_ratelimit.py`, `tests/unit/test_config.py`, `tests/integration/test_web.py`

**Interfaces:**
- Consumes: `client_ip(request, *, hops)`, `AppServices` (4a).
- Produces:
  - `ForwardedLayout(*, hops: int)`, with `.note(request) -> None`. It logs `"A widget post carried %d X-Forwarded-For entries; TRUSTED_PROXY_HOPS is %d"` once, from logger `assistant.ratelimit`.
  - `AppServices.forwarded: ForwardedLayout`.
  - `Settings.trusted_proxy_hops = Field(default=1, ge=1)`.

- [ ] **Step 1: Write the failing tests**

Modify `tests/unit/test_ratelimit.py`:

```diff
--- a/tests/unit/test_ratelimit.py
+++ b/tests/unit/test_ratelimit.py
@@ -1,6 +1,9 @@
+import logging
+
+import pytest
 from starlette.requests import Request
 
-from assistant.ratelimit import SlidingWindow, client_ip
+from assistant.ratelimit import ForwardedLayout, SlidingWindow, client_ip
 
 
 class Clock:
@@ -52,3 +55,18 @@ def test_the_client_is_counted_from_the_right_of_x_forwarded_for() -> None:
 def test_without_the_header_the_connection_is_the_client() -> None:
     assert client_ip(request(None), hops=1) == "10.0.0.9"
     assert client_ip(request("203.0.113.7"), hops=2) == "10.0.0.9"
+
+
+def test_the_first_post_logs_how_many_entries_came_and_no_address(
+    caplog: pytest.LogCaptureFixture,
+) -> None:
+    # No Cloud Run page says how many entries its front end appends: the
+    # first post on each instance shows it, for TRUSTED_PROXY_HOPS.
+    layout = ForwardedLayout(hops=1)
+    with caplog.at_level(logging.INFO, logger="assistant.ratelimit"):
+        layout.note(request("6.6.6.6, 203.0.113.7"))
+        layout.note(request("198.51.100.4"))
+    assert caplog.messages == [
+        "A widget post carried 2 X-Forwarded-For entries; TRUSTED_PROXY_HOPS is 1"
+    ]
+    assert "203.0.113.7" not in caplog.text
```

Modify `tests/unit/test_config.py`:

```diff
--- a/tests/unit/test_config.py
+++ b/tests/unit/test_config.py
@@ -79,6 +79,19 @@ def test_the_environment_still_overrides_the_secret(
     assert Settings(_env_file=None).daily_request_cap == 50  # pyright: ignore[reportCallIssue]
 
 
+@pytest.mark.parametrize("hops", [0, -1])
+def test_the_client_is_never_an_entry_the_client_wrote(hops: int) -> None:
+    # With 0, the address taken would be X-Forwarded-For's leftmost entry,
+    # which a client writes itself: each post could name a new one.
+    with pytest.raises(ValidationError, match="trusted_proxy_hops"):
+        Settings(
+            _env_file=None,  # pyright: ignore[reportCallIssue]
+            assistant_mcp_token="t",
+            customer_ref_secret="s",
+            trusted_proxy_hops=hops,
+        )
+
+
 def test_cloud_tasks_needs_a_project_and_an_invoker() -> None:
     with pytest.raises(ValidationError, match="GCP_PROJECT and TASKS_INVOKER_SA"):
         Settings(
```

Modify `tests/integration/test_web.py`:

```diff
--- a/tests/integration/test_web.py
+++ b/tests/integration/test_web.py
@@ -1,6 +1,7 @@
 """The widget's endpoints, through the whole service in process: the app and
 its lifespan, the local queue, Postgres, the fake Rezerve, a scripted model."""
 
+import logging
 from collections.abc import AsyncIterator
 from datetime import UTC, datetime
 from typing import Any, cast
@@ -144,6 +145,18 @@ async def test_the_widget_keeps_looking_while_the_owner_holds_the_conversation(
     assert (await widget.poll())["with_owner"] is True
 
 
+async def test_the_first_post_shows_the_platforms_forwarding(
+    widget: Widget, script: Script, caplog: pytest.LogCaptureFixture
+) -> None:
+    script.add(says("Merhaba!"), says("Buyrun."))
+    with caplog.at_level(logging.INFO, logger="assistant.ratelimit"):
+        await widget.post("merhaba")
+        await widget.post("bir sorum var")
+    assert caplog.messages == [
+        "A widget post carried 0 X-Forwarded-For entries; TRUSTED_PROXY_HOPS is 1"
+    ]
+
+
 @pytest.mark.parametrize(
     ("slug", "body"),
     [
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/unit/test_ratelimit.py tests/unit/test_config.py tests/integration/test_web.py`
Expected: a collection error, `cannot import name 'ForwardedLayout'`. Without it, the two `hops` cases and the web test fail: 3 failed.

- [ ] **Step 3: Implement**

Modify `src/assistant/ratelimit.py`:

```diff
--- a/src/assistant/ratelimit.py
+++ b/src/assistant/ratelimit.py
@@ -6,12 +6,15 @@ two, so one address gets at most twice the limit. The daily model-request cap
 stays the hard stop; this keeps one address from spending it alone.
 """
 
+import logging
 import time
 from collections import deque
 from collections.abc import Callable
 
 from starlette.requests import Request
 
+log = logging.getLogger(__name__)
+
 
 class SlidingWindow:
     def __init__(
@@ -53,8 +56,32 @@ def client_ip(request: Request, *, hops: int) -> str:
     """The address `hops` places from the right of X-Forwarded-For: one the
     platform in front of the service appended, which a client cannot forge.
     Entries further left came from the client itself."""
-    header = request.headers.get("x-forwarded-for", "")
-    forwarded = [part.strip() for part in header.split(",") if part.strip()]
+    forwarded = _forwarded(request)
     if len(forwarded) >= hops:
         return forwarded[-hops]
     return request.client.host if request.client else "unknown"
+
+
+class ForwardedLayout:
+    """Logs once how many X-Forwarded-For entries a widget post carried, and
+    never an address. No Cloud Run page says how many its front end appends:
+    a post sent with no header of its own shows it, for TRUSTED_PROXY_HOPS."""
+
+    def __init__(self, *, hops: int) -> None:
+        self._hops = hops
+        self._noted = False
+
+    def note(self, request: Request) -> None:
+        if self._noted:
+            return
+        self._noted = True
+        log.info(
+            "A widget post carried %d X-Forwarded-For entries; TRUSTED_PROXY_HOPS is %d",
+            len(_forwarded(request)),
+            self._hops,
+        )
+
+
+def _forwarded(request: Request) -> list[str]:
+    header = request.headers.get("x-forwarded-for", "")
+    return [part.strip() for part in header.split(",") if part.strip()]
```

Modify `src/assistant/config.py`:

```diff
--- a/src/assistant/config.py
+++ b/src/assistant/config.py
@@ -91,8 +91,8 @@ class Settings(DatabaseSettings):
     # The widget's posts per client IP, in the same window.
     ip_message_limit: int = 60
     # Which X-Forwarded-For entry is the client, counted from the right: the
-    # ones the platform appends. Checked on Cloud Run before go-live.
-    trusted_proxy_hops: int = 1
+    # ones the platform appends. Each instance logs how many a post carried.
+    trusted_proxy_hops: int = Field(default=1, ge=1)
 
     # Traces over OTLP/HTTP (spec §7.10): Langfuse's traces endpoint, and its
     # headers as "Authorization=Basic <base64 of public:secret key>".
```

Modify `src/assistant/services.py`:

```diff
--- a/src/assistant/services.py
+++ b/src/assistant/services.py
@@ -11,7 +11,7 @@ from assistant.channels.telegram import TelegramApi
 from assistant.config import Settings
 from assistant.conversation.store import Store
 from assistant.queue.base import TaskQueue
-from assistant.ratelimit import SlidingWindow
+from assistant.ratelimit import ForwardedLayout, SlidingWindow
 
 # Checks a bearer token for an audience and returns its claims (spec §5).
 type TokenVerifier = Callable[[str, str], Awaitable[Mapping[str, Any]]]
@@ -24,6 +24,8 @@ class AppServices:
     queue: TaskQueue
     # The widget's posts per client address.
     post_limit: SlidingWindow
+    # Logs how many X-Forwarded-For entries the instance's first post carried.
+    forwarded: ForwardedLayout
     # Answers a conversation's unprocessed messages: what a turn task runs.
     process_turn: Callable[[int], Awaitable[None]]
     verify_token: TokenVerifier
```

Modify `src/assistant/main.py`:

```diff
--- a/src/assistant/main.py
+++ b/src/assistant/main.py
@@ -24,7 +24,7 @@ from assistant.mcp import OpenRezerve, connect
 from assistant.queue.base import TaskQueue
 from assistant.queue.cloud_tasks import CloudTasksQueue, TaskCreator
 from assistant.queue.local import LocalTaskQueue
-from assistant.ratelimit import SlidingWindow
+from assistant.ratelimit import ForwardedLayout, SlidingWindow
 from assistant.reminders import Reminders
 from assistant.services import AppServices, TokenVerifier
 
@@ -104,6 +104,7 @@ def create_app(
                 post_limit=SlidingWindow(
                     limit=config.ip_message_limit, seconds=config.sender_window_seconds
                 ),
+                forwarded=ForwardedLayout(hops=config.trusted_proxy_hops),
                 process_turn=processor.process,
                 verify_token=verify_token or internal.verify_google_token,
                 telegram=telegram,
```

Modify `src/assistant/channels/web.py`:

```diff
--- a/src/assistant/channels/web.py
+++ b/src/assistant/channels/web.py
@@ -72,6 +72,7 @@ class WebChannel:
 async def post_message(
     slug: Slug, body: PostedMessage, services: Services, request: Request
 ) -> Accepted:
+    services.forwarded.note(request)
     address = client_ip(request, hops=services.settings.trusted_proxy_hops)
     if not services.post_limit.allow(address):
         raise HTTPException(status_code=429, detail="too_many_messages")
```

- [ ] **Step 4: Run the four checks**

Expected: 527 passed; ruff and pyright clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(web): the client is never an entry it wrote; each instance logs the forwarding it sees

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Settings that cannot work, refused; `assistant config check`

4a left no startup checks on the config: its deferred minor. Some settings start a service that cannot work. Each now stops it at startup, by key:
- **A `DATABASE_URL` asyncpg cannot open,** which is the one Neon's console gives:
  - not `postgresql+asyncpg://`;
  - with `sslmode` or `channel_binding`;
  - or Neon's `-pooler` host.
- **A bot token with no webhook secret.** The webhook would refuse every update.
- **A webhook secret Telegram would refuse.**
- **Cloud Tasks with a `PUBLIC_BASE_URL` that is not https.** Its OIDC tokens would go over plain HTTP.

The production config is one JSON file, uploaded as a secret. `assistant config check <file>` finds its problems on your machine first. It reads the file alone: neither the shell's variables nor a `.env` beside it may fill a gap. It names each problem by its key, never its value. It also refuses:
- a key that is no setting;
- a value still `REPLACE_ME`, the example's placeholder;
- with `--expect KEY=VALUE`, a value the deploy set up that differs. `push-config.sh` passes the service's own URL, project and tasks account (Task 8).

**Files:**
- Create: `src/assistant/config_check.py`, `deploy/config.example.json`
- Modify: `src/assistant/config.py`, `src/assistant/cli.py`, `.gitignore`
- Test: `tests/unit/test_config.py`, `tests/unit/test_config_check.py`

**Interfaces:**
- Consumes: `Settings`, `DatabaseSettings`, `cli.main(argv)` (4a).
- Produces:
  - `config.from_file(path: Path) -> Settings`: every setting from the JSON file alone.
  - `config_check.problems(path: Path, expected: dict[str, str]) -> list[str]`, each line `"KEY: message"`.
  - `config_check.PLACEHOLDER = "REPLACE_ME"`.
  - `assistant config check PATH [--expect KEY=VALUE ...]`: it prints `No problems.` and exits 0, or the problems and exits 1.
  - `deploy/config.example.json`, which Tasks 6 and 8 and Go-live use.

- [ ] **Step 1: Write the failing tests, and the example they read**

Create `deploy/config.example.json`:

```json
{
  "DATABASE_URL": "postgresql+asyncpg://neondb_owner:REPLACE_ME@REPLACE_ME.eu-central-1.aws.neon.tech/neondb?ssl=require",
  "ASSISTANT_MCP_TOKEN": "REPLACE_ME",
  "CUSTOMER_REF_SECRET": "REPLACE_ME",
  "ASSISTANT_EVENT_SECRET": "REPLACE_ME",
  "GEMINI_API_KEY": "REPLACE_ME",
  "GROQ_API_KEY": "REPLACE_ME",
  "TELEGRAM_BOT_TOKEN": "REPLACE_ME",
  "TELEGRAM_WEBHOOK_SECRET": "REPLACE_ME",
  "ALLOWED_WEB_ORIGINS": "https://rezerve-iota.vercel.app",
  "REZERVE_MCP_URL": "https://rezerve-iota.vercel.app/api/mcp",
  "PUBLIC_BASE_URL": "https://assistant-REPLACE_ME.europe-west1.run.app",
  "QUEUE_BACKEND": "cloud_tasks",
  "GCP_PROJECT": "REPLACE_ME",
  "TASKS_INVOKER_SA": "assistant-tasks@REPLACE_ME.iam.gserviceaccount.com",
  "OTEL_TRACES_ENDPOINT": "https://cloud.langfuse.com/api/public/otel/v1/traces",
  "OTEL_HEADERS": "Authorization=Basic REPLACE_ME,x-langfuse-ingestion-version=4"
}
```

Modify `tests/unit/test_config.py`:

```diff
--- a/tests/unit/test_config.py
+++ b/tests/unit/test_config.py
@@ -4,12 +4,14 @@ from pathlib import Path
 import pytest
 from pydantic import ValidationError
 
-from assistant.config import Settings
+from assistant.config import DatabaseSettings, Settings
 
 
 @pytest.fixture(autouse=True)
 def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
     for name in (
+        "TELEGRAM_BOT_TOKEN",
+        "TELEGRAM_WEBHOOK_SECRET",
         "ASSISTANT_MCP_TOKEN",
         "CUSTOMER_REF_SECRET",
         "ALLOWED_WEB_ORIGINS",
@@ -53,6 +55,7 @@ def test_production_reads_every_setting_from_one_mounted_secret(
             "ASSISTANT_MCP_TOKEN": "token",
             "CUSTOMER_REF_SECRET": "ref-secret",
             "TELEGRAM_BOT_TOKEN": "123:abc",
+            "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
             "DAILY_REQUEST_CAP": 250,
         },
     )
@@ -100,3 +103,72 @@ def test_cloud_tasks_needs_a_project_and_an_invoker() -> None:
             customer_ref_secret="s",
             queue_backend="cloud_tasks",
         )
+
+
+NEON = "ep-quiet-sun-a1b2c3d4.eu-central-1.aws.neon.tech"
+
+
+@pytest.mark.parametrize(
+    ("url", "problem"),
+    [
+        ("postgresql://u:p@localhost:5433/assistant", r"must start with postgresql\+asyncpg://"),
+        (
+            f"postgresql+asyncpg://u:p@{NEON}/neondb?sslmode=require&channel_binding=require",
+            "asyncpg takes ssl=require",
+        ),
+        (
+            "postgresql+asyncpg://u:p@ep-quiet-sun-a1b2c3d4-pooler.eu-central-1.aws.neon.tech/neondb?ssl=require",
+            "direct endpoint",
+        ),
+    ],
+)
+def test_the_database_url_is_one_asyncpg_opens(url: str, problem: str) -> None:
+    # Neon's console gives "postgresql://…?sslmode=require&channel_binding=require".
+    # asyncpg takes neither parameter: every connection would fail, the
+    # migration job's first.
+    with pytest.raises(ValidationError, match=problem):
+        DatabaseSettings(_env_file=None, database_url=url)  # pyright: ignore[reportCallIssue]
+
+
+def test_neons_direct_endpoint_over_tls_is_accepted() -> None:
+    url = f"postgresql+asyncpg://u:p@{NEON}/neondb?ssl=require"
+    settings = DatabaseSettings(_env_file=None, database_url=url)  # pyright: ignore[reportCallIssue]
+    assert settings.database_url == url
+
+
+def test_a_bot_needs_the_secret_its_webhook_checks() -> None:
+    # Without it the webhook refuses every update: no customer or owner on
+    # Telegram would get an answer.
+    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN needs TELEGRAM_WEBHOOK_SECRET"):
+        Settings(
+            _env_file=None,  # pyright: ignore[reportCallIssue]
+            assistant_mcp_token="t",
+            customer_ref_secret="s",
+            telegram_bot_token="123:abc",
+        )
+
+
+@pytest.mark.parametrize("secret", ["has a space", "x" * 257, "şifre", ""])
+def test_the_webhook_secret_is_one_telegram_accepts(secret: str) -> None:
+    with pytest.raises(ValidationError, match="Telegram accepts 1 to 256"):
+        Settings(
+            _env_file=None,  # pyright: ignore[reportCallIssue]
+            assistant_mcp_token="t",
+            customer_ref_secret="s",
+            telegram_bot_token="123:abc",
+            telegram_webhook_secret=secret,
+        )
+
+
+def test_cloud_tasks_calls_back_over_https() -> None:
+    # Its OIDC token goes with each call: never over plain HTTP.
+    with pytest.raises(ValidationError, match="needs an https PUBLIC_BASE_URL"):
+        Settings(
+            _env_file=None,  # pyright: ignore[reportCallIssue]
+            assistant_mcp_token="t",
+            customer_ref_secret="s",
+            queue_backend="cloud_tasks",
+            gcp_project="p",
+            tasks_invoker_sa="assistant-tasks@p.iam.gserviceaccount.com",
+            public_base_url="http://assistant-1.europe-west1.run.app",
+        )
```

Create `tests/unit/test_config_check.py`:

```python
"""`assistant config check`: the production config, checked on the
operator's machine before deploy/push-config.sh uploads it."""

import json
from pathlib import Path
from typing import Any

import pytest

from assistant.cli import main

EXAMPLE = Path(__file__).parents[2] / "deploy" / "config.example.json"


def example() -> dict[str, str]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def filled() -> dict[str, str]:
    """The example, each placeholder filled in."""
    return {key: value.replace("REPLACE_ME", "x1") for key, value in example().items()}


def check(tmp_path: Path, values: dict[str, Any], *expected: str) -> int:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return main(["config", "check", str(path), *[f"--expect={item}" for item in expected]])


def test_the_example_filled_in_has_no_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(tmp_path, filled()) == 0
    assert capsys.readouterr().out == "No problems.\n"


def test_each_placeholder_left_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check(tmp_path, example()) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{key}: still the example's placeholder"
        for key, value in example().items()
        if "REPLACE_ME" in value
    ]


def test_a_problem_is_named_by_its_key_and_never_shows_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The file holds every secret the service has: a terminal's scrollback
    # is no place for them.
    neon = "postgresql://neondb_owner:hunter2@ep-x1.eu-central-1.aws.neon.tech/neondb"
    values = filled() | {
        "DATABASE_URL": f"{neon}?sslmode=require&channel_binding=require",
        "DAILY_REQUEST_CAP": "lots-of-them",
        "GEMINI_APIKEY": "AIza-misplaced",
    }
    assert check(tmp_path, values) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "GEMINI_APIKEY: not a setting",
        "DATABASE_URL: must start with postgresql+asyncpg://",
        "DAILY_REQUEST_CAP: Input should be a valid integer, unable to parse string as an integer",
    ]
    for value in ("hunter2", "lots-of-them", "AIza"):
        assert value not in out


def test_the_values_the_deploy_set_up_are_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A PUBLIC_BASE_URL that is not the service's own fails every Cloud Tasks
    # call back: its token is minted for the wrong audience.
    url = "https://assistant-123456789012.europe-west1.run.app"
    values = filled() | {"PUBLIC_BASE_URL": url, "GCP_PROJECT": "rezerve-assistant"}
    deployed = [
        f"PUBLIC_BASE_URL={url}",
        "GCP_PROJECT=rezerve-assistant",
        "GCP_REGION=europe-west1",
        "QUEUE_BACKEND=cloud_tasks",
    ]
    assert check(tmp_path, values, *deployed) == 0
    assert check(tmp_path, values, "GCP_PROJECT=rezerve-assistant-2", "NO_SUCH=1") == 1
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "GCP_PROJECT: should be rezerve-assistant-2",
        "NO_SUCH: not a setting",
    ]


def test_only_the_file_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The operator runs the check beside their own .env and shell variables:
    # neither may fill a gap the file has.
    monkeypatch.setenv("CUSTOMER_REF_SECRET", "from-the-shell")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CUSTOMER_REF_SECRET=from-dotenv\n", encoding="utf-8")
    values = filled()
    del values["CUSTOMER_REF_SECRET"]
    assert check(tmp_path, values) == 1
    assert capsys.readouterr().out == "CUSTOMER_REF_SECRET: Field required\n"


def test_a_file_that_is_not_json_is_named_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"ASSISTANT_MCP_TOKEN": "secret-token",}', encoding="utf-8")
    assert main(["config", "check", str(path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Not a JSON object: ")
    assert "secret-token" not in out
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/unit/test_config.py tests/unit/test_config_check.py`
Expected: 15 failed. The validators do not exist yet, and argparse has no `config` command, so it raises `SystemExit: 2`.

- [ ] **Step 3: Implement**

Modify `src/assistant/config.py`:

```diff
--- a/src/assistant/config.py
+++ b/src/assistant/config.py
@@ -7,7 +7,10 @@ Secret Manager secret (JSON), mounted as a file named by CONFIG_FILE (spec
 """
 
 import os
+import re
+from pathlib import Path
 from typing import Annotated, Literal, Self
+from urllib.parse import parse_qs, urlsplit
 
 from pydantic import Field, SecretStr, field_validator, model_validator
 from pydantic_settings import (
@@ -26,6 +29,21 @@ class DatabaseSettings(BaseSettings):
 
     database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/assistant"
 
+    @field_validator("database_url")
+    @classmethod
+    def _asyncpg_opens(cls, url: str) -> str:
+        """Neon's console gives "postgresql://…?sslmode=require&channel_binding=require":
+        asyncpg takes neither parameter, so every connection would fail. Its
+        direct endpoint is the one used: two instances need no pooler."""
+        parts = urlsplit(url)
+        if parts.scheme != "postgresql+asyncpg":
+            raise ValueError("must start with postgresql+asyncpg://")
+        if {"sslmode", "channel_binding"} & parse_qs(parts.query).keys():
+            raise ValueError("asyncpg takes ssl=require, not sslmode or channel_binding")
+        if "-pooler." in (parts.hostname or ""):
+            raise ValueError("use Neon's direct endpoint: the host without -pooler")
+        return url
+
     @classmethod
     def settings_customise_sources(
         cls,
@@ -108,8 +126,43 @@ class Settings(DatabaseSettings):
             return [item.strip() for item in value.split(",") if item.strip()]
         return value
 
+    @field_validator("telegram_webhook_secret")
+    @classmethod
+    def _telegram_accepts(cls, secret: SecretStr | None) -> SecretStr | None:
+        if secret is not None and not re.fullmatch(
+            r"[A-Za-z0-9_-]{1,256}", secret.get_secret_value()
+        ):
+            raise ValueError("Telegram accepts 1 to 256 letters, digits, _ and -")
+        return secret
+
     @model_validator(mode="after")
-    def _cloud_tasks_is_complete(self) -> Self:
-        if self.queue_backend == "cloud_tasks" and not (self.gcp_project and self.tasks_invoker_sa):
-            raise ValueError("QUEUE_BACKEND=cloud_tasks needs GCP_PROJECT and TASKS_INVOKER_SA")
+    def _settings_agree(self) -> Self:
+        if self.queue_backend == "cloud_tasks":
+            if not (self.gcp_project and self.tasks_invoker_sa):
+                raise ValueError("QUEUE_BACKEND=cloud_tasks needs GCP_PROJECT and TASKS_INVOKER_SA")
+            if not self.public_base_url.startswith("https://"):
+                raise ValueError("QUEUE_BACKEND=cloud_tasks needs an https PUBLIC_BASE_URL")
+        if self.telegram_bot_token is not None and self.telegram_webhook_secret is None:
+            # The webhook would refuse every update.
+            raise ValueError("TELEGRAM_BOT_TOKEN needs TELEGRAM_WEBHOOK_SECRET")
         return self
+
+
+def from_file(path: Path) -> Settings:
+    """Every setting from this JSON file alone: the operator's commands read
+    the production config this way, so neither their shell's variables nor a
+    .env beside them fills a gap the file has."""
+
+    class FileOnly(Settings):
+        @classmethod
+        def settings_customise_sources(
+            cls,
+            settings_cls: type[BaseSettings],
+            init_settings: PydanticBaseSettingsSource,
+            env_settings: PydanticBaseSettingsSource,
+            dotenv_settings: PydanticBaseSettingsSource,
+            file_secret_settings: PydanticBaseSettingsSource,
+        ) -> tuple[PydanticBaseSettingsSource, ...]:
+            return (JsonConfigSettingsSource(settings_cls, json_file=path),)
+
+    return FileOnly()  # pyright: ignore[reportCallIssue]
```

Create `src/assistant/config_check.py`:

```python
"""`assistant config check <file> [--expect KEY=VALUE ...]`: the production
config, checked on the operator's machine before deploy/push-config.sh
uploads it. Only the file counts, never the environment or a .env beside it.
Problems are named by key and never show a value: the file holds every
secret the service has."""

import json
from pathlib import Path
from typing import cast

from pydantic import SecretStr, ValidationError

from assistant.config import Settings, from_file

# What deploy/config.example.json holds where a value is still to be filled in.
PLACEHOLDER = "REPLACE_ME"


def problems(path: Path, expected: dict[str, str]) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"Not a JSON object: {error.msg}, line {error.lineno}"]
    if not isinstance(raw, dict):
        return ["Not a JSON object"]
    values = cast(dict[str, object], raw)
    found = [f"{key}: not a setting" for key in values if not _is_setting(key)]
    found += [
        f"{key}: still the example's placeholder"
        for key, value in values.items()
        if isinstance(value, str) and PLACEHOLDER in value
    ]
    try:
        settings = from_file(path)
    except ValidationError as error:
        return found + [_line(detail["loc"], detail["msg"]) for detail in error.errors()]
    for key, value in expected.items():
        if not _is_setting(key):
            found.append(f"{key}: not a setting")
        elif _shown(getattr(settings, key.lower())) != value:
            found.append(f"{key}: should be {value}")
    return found


def _is_setting(key: str) -> bool:
    return key.lower() in Settings.model_fields


def _shown(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return "" if value is None else str(value)


def _line(location: tuple[int | str, ...], message: str) -> str:
    # Pydantic's messages for a validator's ValueError start "Value error, ".
    message = message.removeprefix("Value error, ")
    return f"{str(location[0]).upper()}: {message}" if location else message
```

Modify `src/assistant/cli.py`:

```diff
--- a/src/assistant/cli.py
+++ b/src/assistant/cli.py
@@ -1,14 +1,20 @@
-"""Admin commands: `uv run assistant owners link <slug> <telegram_chat_id>`.
+"""Admin commands.
 
-A business owner learns their chat's id by sending /id to the bot. Linking it
-sends that business's handoffs to the owner (spec §7.6); a self-service
-pairing in the Rezerve panel is v2.
+`uv run assistant owners link <slug> <telegram_chat_id>`: a business owner
+learns their chat's id by sending /id to the bot. Linking it sends that
+business's handoffs to the owner (spec §7.6); a self-service pairing in the
+Rezerve panel is v2.
+
+`uv run assistant config check <file>`: the production config, before it is
+uploaded (assistant.config_check).
 """
 
 import argparse
 import asyncio
 from collections.abc import Sequence
+from pathlib import Path
 
+from assistant import config_check
 from assistant.config import DatabaseSettings
 from assistant.conversation.store import Store
 from assistant.db.session import create_engine, session_factory
@@ -24,6 +30,17 @@ def parse(argv: Sequence[str] | None) -> argparse.Namespace:
     link.add_argument("chat_id", type=int)
     unlink = actions.add_parser("unlink", help="stop; handoffs give the business's phone again")
     unlink.add_argument("slug")
+    config = groups.add_parser("config", help="the production config file")
+    checks = config.add_subparsers(dest="action", required=True)
+    check = checks.add_parser("check", help="find its problems, naming keys, never values")
+    check.add_argument("path", type=Path)
+    check.add_argument(
+        "--expect",
+        action="append",
+        default=[],
+        metavar="KEY=VALUE",
+        help="a value the deploy set up, such as PUBLIC_BASE_URL",
+    )
     return parser.parse_args(argv)
 
 
@@ -41,5 +58,11 @@ async def run(args: argparse.Namespace) -> str:
 
 
 def main(argv: Sequence[str] | None = None) -> int:
-    print(asyncio.run(run(parse(argv))))
+    args = parse(argv)
+    if args.group == "config":
+        expected = dict(item.partition("=")[::2] for item in args.expect)
+        found = config_check.problems(args.path, expected)
+        print("\n".join(found) if found else "No problems.")
+        return 1 if found else 0
+    print(asyncio.run(run(args)))
     return 0
```

Modify `.gitignore`:

```diff
--- a/.gitignore
+++ b/.gitignore
@@ -5,3 +5,4 @@ __pycache__/
 .ruff_cache/
 .superpowers/
 evals/.cache/
+deploy/config.json
```

- [ ] **Step 4: Run the four checks, and the command**

```bash
uv run assistant config check deploy/config.example.json
```

Expected: one `still the example's placeholder` line per value to fill, and exit 1. Then 543 passed; ruff and pyright clean. Your own `.env` sets none of the keys this task checks, so local runs are unaffected.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(config): settings that cannot work are refused at startup; assistant config check names them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: `assistant telegram webhook`

Spec §7.9: Telegram is told where to post the bot's updates, with a `secret_token` the webhook checks. `assistant telegram webhook <file>` does it from the production config.
- It checks the file first, and sets nothing if the file has a problem.
- It calls `setWebhook` with `allowed_updates: ["message"]`: the webhook reads nothing else.
- Then it prints Telegram's own view: the URL, how many updates wait, and the last delivery error. A wrong secret shows there as the webhook's 404.

It never prints the token. `TelegramError`'s message carries Telegram's answer, never the URL.

**Files:**
- Modify: `src/assistant/channels/telegram.py`, `src/assistant/cli.py`, `tests/fakes/telegram.py`
- Test: `tests/unit/test_cli_telegram.py`

**Interfaces:**
- Consumes: `TelegramApi`, `TelegramError` (4a); `config.from_file`, `config_check.problems` (Task 5).
- Produces:
  - `TelegramApi.set_webhook(url: str, secret: str) -> None`;
  - `TelegramApi.webhook_info() -> dict[str, Any]`;
  - `cli.main(argv=None, *, telegram: httpx.AsyncBaseTransport | None = None) -> int`, where tests pass Telegram's transport;
  - `FakeTelegram.webhook: dict[str, Any]`, what `getWebhookInfo` answers.

- [ ] **Step 1: Write the failing tests, and teach the fake the two calls**

Modify `tests/fakes/telegram.py`:

```diff
--- a/tests/fakes/telegram.py
+++ b/tests/fakes/telegram.py
@@ -15,6 +15,8 @@ class FakeTelegram:
         self._next_id = 100
         # An error Telegram returns to every call from now on, as (status, description).
         self.fail_with: tuple[int, str] | None = None
+        # What getWebhookInfo answers; setWebhook sets its url.
+        self.webhook: dict[str, Any] = {"url": "", "pending_update_count": 0}
 
     @property
     def transport(self) -> httpx.MockTransport:
@@ -29,6 +31,11 @@ class FakeTelegram:
             return httpx.Response(
                 status, json={"ok": False, "error_code": status, "description": description}
             )
+        if method == "setWebhook":
+            self.webhook["url"] = payload["url"]
+            return httpx.Response(200, json={"ok": True, "result": True})
+        if method == "getWebhookInfo":
+            return httpx.Response(200, json={"ok": True, "result": self.webhook})
         self._next_id += 1
         self.delivered.append((int(payload.get("chat_id", 0)), self._next_id))
         return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_id}})
```

Create `tests/unit/test_cli_telegram.py`:

```python
"""`assistant telegram webhook <file>`: Telegram told where to send the bot's
updates, and with which secret (spec §7.9), from the production config."""

import json
from pathlib import Path

import pytest

from assistant.cli import main
from tests.fakes.telegram import FakeTelegram

SERVICE = "https://assistant-123456789012.europe-west1.run.app"
TOKEN = "123456:bot-token"
SECRET = "webhook-secret"


def config(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "ASSISTANT_MCP_TOKEN": "t",
        "CUSTOMER_REF_SECRET": "s",
        "PUBLIC_BASE_URL": SERVICE,
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "TELEGRAM_WEBHOOK_SECRET": SECRET,
    } | overrides
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_telegram_sends_the_bots_messages_to_this_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    code = main(["telegram", "webhook", str(config(tmp_path))], telegram=telegram.transport)
    assert code == 0
    assert telegram.calls == [
        (
            "setWebhook",
            {
                "url": f"{SERVICE}/telegram/webhook",
                "secret_token": SECRET,
                # The webhook reads messages; edits, reactions and the rest
                # would be requests for nothing.
                "allowed_updates": ["message"],
            },
        ),
        ("getWebhookInfo", {}),
    ]
    out = capsys.readouterr().out
    assert out.splitlines() == [
        f"Webhook: {SERVICE}/telegram/webhook",
        "Pending updates: 0",
        "Last error: none",
    ]


def test_telegrams_last_error_is_shown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Telegram keeps the last failure delivering an update: a wrong secret
    # shows here as the webhook's 404.
    telegram = FakeTelegram()
    telegram.webhook |= {
        "pending_update_count": 3,
        "last_error_message": "Wrong response from the webhook: 404 Not Found",
    }
    main(["telegram", "webhook", str(config(tmp_path))], telegram=telegram.transport)
    assert capsys.readouterr().out.splitlines()[1:] == [
        "Pending updates: 3",
        "Last error: Wrong response from the webhook: 404 Not Found",
    ]


def test_a_refusal_is_shown_without_the_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    telegram.fail_with = (401, "Unauthorized")
    assert main(["telegram", "webhook", str(config(tmp_path))], telegram=telegram.transport) == 1
    out = capsys.readouterr().out
    assert out == "Telegram refused the call: 401 Unauthorized\n"


def test_a_config_with_problems_sets_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    path = config(tmp_path, PUBLIC_BASE_URL="http://localhost:8000")
    assert main(["telegram", "webhook", str(path)], telegram=telegram.transport) == 1
    assert telegram.calls == []
    # Telegram calls only https URLs; a local service is reached some other way.
    assert capsys.readouterr().out == "PUBLIC_BASE_URL: Telegram calls only https URLs\n"


def test_without_a_bot_there_is_nothing_to_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telegram = FakeTelegram()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"ASSISTANT_MCP_TOKEN": "t", "CUSTOMER_REF_SECRET": "s"}), encoding="utf-8"
    )
    assert main(["telegram", "webhook", str(path)], telegram=telegram.transport) == 1
    assert telegram.calls == []
    assert capsys.readouterr().out == "TELEGRAM_BOT_TOKEN: not in the file\n"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/unit/test_cli_telegram.py`
Expected: 5 failed. `main` takes no `telegram` argument, and there is no `telegram` command.

- [ ] **Step 3: Implement**

Modify `src/assistant/channels/telegram.py`:

```diff
--- a/src/assistant/channels/telegram.py
+++ b/src/assistant/channels/telegram.py
@@ -53,7 +53,16 @@ class TelegramApi:
             }
         return int((await self._call("sendMessage", payload))["message_id"])
 
-    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
+    async def set_webhook(self, url: str, secret: str) -> None:
+        """Telegram posts the bot's messages to `url`, `secret` in a header."""
+        payload = {"url": url, "secret_token": secret, "allowed_updates": ["message"]}
+        await self._call("setWebhook", payload)
+
+    async def webhook_info(self) -> dict[str, Any]:
+        """Where Telegram posts updates, how many wait, and its last failure."""
+        return await self._call("getWebhookInfo", {})
+
+    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
         try:
             response = await self._client.post(f"{self._base}/{method}", json=payload)
         except httpx.HTTPError as error:
@@ -63,8 +72,7 @@ class TelegramApi:
         if not body.get("ok"):
             status = int(body.get("error_code") or response.status_code)
             raise TelegramError(status, str(body.get("description", "")))
-        result: dict[str, Any] = body["result"]
-        return result
+        return body["result"]
 
 
 class TelegramChannel:
```

Modify `src/assistant/cli.py`:

```diff
--- a/src/assistant/cli.py
+++ b/src/assistant/cli.py
@@ -7,6 +7,9 @@ Rezerve panel is v2.
 
 `uv run assistant config check <file>`: the production config, before it is
 uploaded (assistant.config_check).
+
+`uv run assistant telegram webhook <file>`: Telegram told to post the bot's
+messages to the service that config describes.
 """
 
 import argparse
@@ -14,8 +17,11 @@ import asyncio
 from collections.abc import Sequence
 from pathlib import Path
 
+import httpx
+
 from assistant import config_check
-from assistant.config import DatabaseSettings
+from assistant.channels.telegram import TelegramApi, TelegramError
+from assistant.config import DatabaseSettings, from_file
 from assistant.conversation.store import Store
 from assistant.db.session import create_engine, session_factory
 
@@ -41,6 +47,10 @@ def parse(argv: Sequence[str] | None) -> argparse.Namespace:
         metavar="KEY=VALUE",
         help="a value the deploy set up, such as PUBLIC_BASE_URL",
     )
+    telegram = groups.add_parser("telegram", help="the Telegram bot")
+    bot = telegram.add_subparsers(dest="action", required=True)
+    webhook = bot.add_parser("webhook", help="send the bot's messages to the configured service")
+    webhook.add_argument("path", type=Path)
     return parser.parse_args(argv)
 
 
@@ -57,8 +67,41 @@ async def run(args: argparse.Namespace) -> str:
         await engine.dispose()
 
 
-def main(argv: Sequence[str] | None = None) -> int:
+async def set_webhook(path: Path, transport: httpx.AsyncBaseTransport | None) -> int:
+    if found := config_check.problems(path, {}):
+        print("\n".join(found))
+        return 1
+    settings = from_file(path)
+    token, secret = settings.telegram_bot_token, settings.telegram_webhook_secret
+    if token is None or secret is None:
+        print("TELEGRAM_BOT_TOKEN: not in the file")
+        return 1
+    if not settings.public_base_url.startswith("https://"):
+        print("PUBLIC_BASE_URL: Telegram calls only https URLs")
+        return 1
+    url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
+    async with httpx.AsyncClient(transport=transport, timeout=15) as client:
+        api = TelegramApi(token.get_secret_value(), client)
+        try:
+            await api.set_webhook(url, secret.get_secret_value())
+            info = await api.webhook_info()
+        except TelegramError as error:
+            # Its message has Telegram's answer, never the URL with the token.
+            print(error)
+            return 1
+    print(f"Webhook: {info.get('url')}")
+    print(f"Pending updates: {info.get('pending_update_count', 0)}")
+    print(f"Last error: {info.get('last_error_message') or 'none'}")
+    return 0
+
+
+def main(
+    argv: Sequence[str] | None = None, *, telegram: httpx.AsyncBaseTransport | None = None
+) -> int:
+    """`telegram` stands in for Telegram's API in tests."""
     args = parse(argv)
+    if args.group == "telegram":
+        return asyncio.run(set_webhook(args.path, telegram))
     if args.group == "config":
         expected = dict(item.partition("=")[::2] for item in args.expect)
         found = config_check.problems(args.path, expected)
```

- [ ] **Step 4: Run the four checks**

Expected: 548 passed; ruff and pyright clean. `uv run assistant telegram --help` lists `webhook`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(telegram): assistant telegram webhook points the bot at the service and shows Telegram's view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7: CI runs the image as the deploy will

CI has built the image without running it. Now it runs what the deploy will run:
- the migration job's command, `alembic upgrade head`, on an empty database;
- the service on `PORT=8080`, asked for `/health`;
- every log line read as JSON.

The image needs a model key to start, so CI gives it a dummy one. A new `workflows` job lints the workflow files with actionlint, which runs shellcheck on each step's script. Task 8 adds the deploy scripts to it.

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the image's `python -m assistant.serve` (Task 3).
- Produces: the jobs `test`, `image` and `workflows`, which Task 9's `deploy` job needs.

- [ ] **Step 1: See what CI will run, locally, before writing it**

A database of its own on the Compose Postgres, then the image's two commands. `host.docker.internal` stands in for CI's `--network host`:

```bash
docker compose exec db createdb -U postgres assistant_imagecheck
DB=postgresql+asyncpg://postgres:postgres@host.docker.internal:5433/assistant_imagecheck
docker build -q -t rezerve-assistant:ci .
docker run --rm --add-host host.docker.internal:host-gateway -e DATABASE_URL="$DB" \
  rezerve-assistant:ci alembic upgrade head
docker run -d --name assistant-ci --add-host host.docker.internal:host-gateway -p 18081:8080 \
  -e PORT=8080 -e DATABASE_URL="$DB" -e ASSISTANT_MCP_TOKEN=ci -e CUSTOMER_REF_SECRET=ci \
  rezerve-assistant:ci
sleep 5; docker logs assistant-ci 2>&1 | tail -2; docker rm -f assistant-ci
```

Expected: migrations 0001 to 0008 run. The service then exits: `No model has an API key: set GEMINI_API_KEY or GROQ_API_KEY.` That is why CI passes a dummy key. Run the second container again with `-e GEMINI_API_KEY=ci`: `curl -fsS localhost:18081/health` answers `{"status":"ok"}`. Then drop the database with `docker compose exec db dropdb -U postgres assistant_imagecheck`.

- [ ] **Step 2: Write the jobs**

Modify `.github/workflows/ci.yml`:

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1,5 +1,6 @@
 # Every push and pull request: lint, types, the test suite against Postgres,
-# and the image. The evals stay out: they spend free-tier model quota.
+# the image as the deploy runs it, and the workflows themselves. The evals
+# stay out: they spend free-tier model quota.
 name: CI
 
 on:
@@ -45,13 +46,55 @@ jobs:
 
   image:
     runs-on: ubuntu-latest
+    services:
+      postgres:
+        image: postgres:17
+        env:
+          POSTGRES_USER: postgres
+          POSTGRES_PASSWORD: postgres
+          POSTGRES_DB: assistant
+        ports:
+          - 5432:5432
+        options: >-
+          --health-cmd "pg_isready -U postgres -d assistant"
+          --health-interval 2s
+          --health-timeout 5s
+          --health-retries 30
+    env:
+      DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/assistant
     steps:
       - uses: actions/checkout@v7
       - uses: docker/setup-buildx-action@v4
       - uses: docker/build-push-action@v7
         with:
           context: .
-          push: false
+          load: true
           tags: rezerve-assistant:ci
           cache-from: type=gha
           cache-to: type=gha,mode=max
+      # What the deploy runs: the migration job's command on a new database,
+      # then the service, on the port Cloud Run gives it.
+      - name: Migrate an empty database with the image
+        run: docker run --rm --network host -e DATABASE_URL rezerve-assistant:ci alembic upgrade head
+      - name: Start the image and ask for /health
+        run: |
+          docker run -d --name assistant --network host -e PORT=8080 -e DATABASE_URL \
+            -e ASSISTANT_MCP_TOKEN=ci -e CUSTOMER_REF_SECRET=ci -e GEMINI_API_KEY=ci \
+            rezerve-assistant:ci
+          for _ in $(seq 30); do
+            if curl -fsS localhost:8080/health; then break; fi
+            sleep 1
+          done
+          curl -fsS localhost:8080/health
+      - name: Its log lines are JSON, for Cloud Logging
+        if: always()
+        run: |
+          docker logs assistant 2>&1 | tee assistant.log
+          python3 -c 'import json, sys; [json.loads(line) for line in open(sys.argv[1])]' assistant.log
+
+  workflows:
+    runs-on: ubuntu-latest
+    steps:
+      - uses: actions/checkout@v7
+      # With shellcheck on each step's script.
+      - run: docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -color
```

- [ ] **Step 3: Lint the workflow, and see the lint catch a planted bug**

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -color
cp .github/workflows/ci.yml "$TMPDIR/ci.yml"
sed -i '' 's|tee assistant.log|tee $UNQUOTED|' .github/workflows/ci.yml
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 | head -1
cp "$TMPDIR/ci.yml" .github/workflows/ci.yml
```

Expected: the first run prints nothing and exits 0. The second reports `SC2086` (unquoted), from shellcheck inside actionlint. The last line puts the file back as Step 2 wrote it. The four checks are unchanged: 548 passed.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: the image migrates a new database and answers /health; the workflows are linted

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: The project, set up once; the config, checked and uploaded

`deploy/setup-gcp.sh` is spec §9's project, for you to run once. It runs again safely: what exists is kept, and what may drift is applied again. You run it with `PROJECT` and `BILLING_ACCOUNT`; `DRY_RUN=1` shows each change and makes none. It sets up:
- **The project,** linked to the billing account.
- **The APIs.**
- **Artifact Registry,** without paid scanning, keeping the last 3 images.
- **The `turns` queue** at spec §9's rates (2/s, 3 at once, 6 attempts, 2–60 s backoff), and **`reminders`** on the defaults.
- **Three service accounts:**
  - the service, `assistant-runtime`;
  - Cloud Tasks' call backs, `assistant-tasks`;
  - GitHub's deploys, `assistant-deployer`.
- **The secret `assistant-config`,** replicated in europe-west1 only.
- **The grants,** listed in the test as the exact set: nothing more.
- **GitHub's keyless access,** from `Olbioss/rezerve-assistant`'s `main` only.
- **A budget alert** at 50% and 100% of 1 in the account's currency.

It ends by printing the two GitHub variables and the three config values the deploy set up.

`deploy/push-config.sh` uploads the config:
- It runs `assistant config check`, expecting the service's own URL, project, region, queue backend and tasks account.
- It adds the file as the secret's new version.
- It moves a running service to that version.
- It destroys all but the newest two: Secret Manager's free tier is 6 versions.

Both are tested against a gcloud stood in for on PATH. Each call is recorded; a describe finds only what exists; a create makes it exist.

**Files:**
- Create: `deploy/setup-gcp.sh`, `deploy/push-config.sh`, `deploy/cleanup-policy.json`, `tests/fakes/gcloud.py`
- Modify: `.github/workflows/ci.yml` (shellcheck)
- Test: `tests/unit/test_setup_gcp.py`, `tests/unit/test_push_config.py`

**Interfaces:**
- Consumes: `assistant config check --expect` (Task 5); `deploy/config.example.json`.
- Produces:
  - **The resource names Task 9's deploy job uses:**
    - the repository `assistant`, image `assistant`;
    - the secret `assistant-config`, mounted at `/secrets/config.json`;
    - the accounts `assistant-runtime@` and `assistant-deployer@`.
  - **The GitHub variables** `GCP_PROJECT` and `GCP_WIF_PROVIDER`.
  - **`FakeGcloud(directory)`,** with:
    - `exists(*words)` and `answers(words, output)`;
    - `calls()` and `changes()`;
    - `run(script, *args, **env) -> CompletedProcess`.

- [ ] **Step 1: Write the fake and the failing tests**

Create `tests/fakes/gcloud.py`:

```python
"""The gcloud CLI, stood in for on PATH: each call is kept, a describe finds
only what exists, a create makes it exist, and a command can be given what
it prints."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

# The fake itself, run as `gcloud` in the scripts' own process.
PROGRAM = """
import json, os, sys

state_path = os.environ["FAKE_GCLOUD_STATE"]
with open(state_path) as file:
    state = json.load(file)
args = sys.argv[1:]
state["calls"].append(args)
words = " ".join(arg for arg in args if not arg.startswith("-"))
code = 0
if " create " in f" {words} " and "identity" not in words:
    state["existing"].append(words.replace(" create ", " describe ", 1))
answer = next((state["answers"][key] for key in state["answers"] if words.startswith(key)), None)
if " describe " in f" {words} " and words not in state["existing"]:
    print(f"ERROR: NOT_FOUND: {words}", file=sys.stderr)
    code = 1
elif answer is not None:
    print(answer)
with open(state_path, "w") as file:
    json.dump(state, file)
sys.exit(code)
"""


class FakeGcloud:
    def __init__(self, directory: Path) -> None:
        self.bin = directory / "bin"
        self.bin.mkdir()
        program = self.bin / "gcloud"
        program.write_text(f"#!{sys.executable}\n{PROGRAM}", encoding="utf-8")
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        self.state = directory / "gcloud.json"
        self._write({"calls": [], "existing": [], "answers": {}})

    def exists(self, *described: str) -> None:
        """Each, as its describe command's words: "projects describe p"."""
        self._update(lambda state: state["existing"].extend(described))

    def answers(self, words: str, output: str) -> None:
        """What a command starting with these words prints; a describe, only
        once what it describes exists."""
        self._update(lambda state: state["answers"].__setitem__(words, output))

    def calls(self) -> list[str]:
        return [" ".join(call) for call in self._read()["calls"]]

    def changes(self) -> list[str]:
        """The calls that change something: no describe, list or lookup."""
        reads = (" describe ", " list ")
        return [call for call in self.calls() if not any(read in f" {call} " for read in reads)]

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        environment = (
            os.environ
            | {
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "FAKE_GCLOUD_STATE": str(self.state),
            }
            | env
        )
        return subprocess.run(
            ["bash", script, *args], capture_output=True, text=True, env=environment, timeout=120
        )

    def _read(self) -> dict[str, Any]:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def _write(self, state: dict[str, Any]) -> None:
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def _update(self, change: Any) -> None:
        state = self._read()
        change(state)
        self._write(state)
```

Create `tests/unit/test_setup_gcp.py`:

```python
"""deploy/setup-gcp.sh, run against a stand-in gcloud: what a new project
gets, what a second run leaves alone, and who may do what."""

from pathlib import Path

import pytest

from tests.fakes.gcloud import FakeGcloud

SCRIPT = str(Path(__file__).parents[2] / "deploy" / "setup-gcp.sh")
PROJECT = "rezerve-assistant"
NUMBER = "123456789012"
BILLING = "0A0A0A-0A0A0A-0A0A0A"
RUNTIME = f"serviceAccount:assistant-runtime@{PROJECT}.iam.gserviceaccount.com"
TASKS = f"assistant-tasks@{PROJECT}.iam.gserviceaccount.com"
DEPLOYER = f"assistant-deployer@{PROJECT}.iam.gserviceaccount.com"
POOL = f"projects/{NUMBER}/locations/global/workloadIdentityPools/github"

# Every grant the service needs, and none more: (on what, to whom, which role).
GRANTS = {
    (f"projects {PROJECT}", RUNTIME, "roles/cloudtasks.enqueuer"),
    (f"service-accounts {TASKS}", RUNTIME, "roles/iam.serviceAccountUser"),
    ("secrets assistant-config", RUNTIME, "roles/secretmanager.secretAccessor"),
    (
        f"service-accounts {TASKS}",
        f"serviceAccount:service-{NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
        "roles/iam.serviceAccountUser",
    ),
    (f"projects {PROJECT}", f"serviceAccount:{DEPLOYER}", "roles/run.admin"),
    (
        f"service-accounts assistant-runtime@{PROJECT}.iam.gserviceaccount.com",
        f"serviceAccount:{DEPLOYER}",
        "roles/iam.serviceAccountUser",
    ),
    ("repositories assistant", f"serviceAccount:{DEPLOYER}", "roles/artifactregistry.writer"),
    ("secrets assistant-config", f"serviceAccount:{DEPLOYER}", "roles/secretmanager.viewer"),
    (
        f"service-accounts {DEPLOYER}",
        f"principalSet://iam.googleapis.com/{POOL}/attribute.repository/Olbioss/rezerve-assistant",
        "roles/iam.workloadIdentityUser",
    ),
}

EVERYTHING = [
    f"projects describe {PROJECT}",
    f"billing projects describe {PROJECT}",
    "artifacts repositories describe assistant",
    "tasks queues describe turns",
    "tasks queues describe reminders",
    *[
        f"iam service-accounts describe {name}@{PROJECT}.iam.gserviceaccount.com"
        for name in ("assistant-runtime", "assistant-tasks", "assistant-deployer")
    ],
    "secrets describe assistant-config",
    "iam workload-identity-pools describe github",
    "iam workload-identity-pools providers describe github",
]


@pytest.fixture
def gcloud(tmp_path: Path) -> FakeGcloud:
    fake = FakeGcloud(tmp_path)
    fake.answers(f"projects describe {PROJECT}", NUMBER)
    return fake


def setup(gcloud: FakeGcloud, **env: str) -> str:
    done = gcloud.run(SCRIPT, **({"PROJECT": PROJECT, "BILLING_ACCOUNT": BILLING} | env))
    assert done.returncode == 0, done.stderr
    return done.stdout


def flag(call: str, name: str) -> str:
    return next(part.split("=", 1)[1] for part in call.split(" --")[1:] if part.startswith(name))


def grants(gcloud: FakeGcloud) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for call in gcloud.changes():
        command, binding, rest = call.partition(" add-iam-policy-binding ")
        if binding:
            kind, target = command.split(" ")[-1], rest.split(" ")[0]
            found.add((f"{kind} {target}", flag(call, "member="), flag(call, "role=")))
    return found


def created(gcloud: FakeGcloud) -> list[str]:
    return [
        call.split(" --")[0]
        for call in gcloud.changes()
        if " create" in call.split(" --")[0] and "identity create" not in call
    ]


def test_a_new_project_gets_everything_the_service_needs(gcloud: FakeGcloud) -> None:
    out = setup(gcloud)
    assert created(gcloud) == [
        f"projects create {PROJECT}",
        "artifacts repositories create assistant",
        "tasks queues create turns",
        "tasks queues create reminders",
        "iam service-accounts create assistant-runtime",
        "iam service-accounts create assistant-tasks",
        "iam service-accounts create assistant-deployer",
        "secrets create assistant-config",
        "iam workload-identity-pools create github",
        "iam workload-identity-pools providers create-oidc github",
        "billing budgets create",
    ]
    changes = gcloud.changes()
    assert (
        f"billing projects link {PROJECT} --billing-account={BILLING} --quiet --format=none"
        in changes
    )
    [turns] = [call for call in changes if call.startswith("tasks queues create turns")]
    # Spec §9: about 2 a second, 3 at once, 6 attempts, backing off 2 to 60 s.
    for rate in (
        "--max-dispatches-per-second=2",
        "--max-concurrent-dispatches=3",
        "--max-attempts=6",
        "--min-backoff=2s",
        "--max-backoff=60s",
    ):
        assert rate in turns
    [registry] = [call for call in changes if call.startswith("artifacts repositories create")]
    assert "--disable-vulnerability-scanning" in registry
    [secret] = [call for call in changes if call.startswith("secrets create")]
    assert "--locations=europe-west1" in secret
    [provider] = [call for call in changes if " providers create-oidc " in call]
    assert flag(provider, "attribute-condition=") == (
        "assertion.repository == 'Olbioss/rezerve-assistant' && assertion.ref == 'refs/heads/main'"
    )
    [budget] = [call for call in changes if call.startswith("billing budgets create")]
    assert flag(budget, "budget-amount=") == "1"
    assert flag(budget, "filter-projects=") == f"projects/{PROJECT}"
    assert grants(gcloud) == GRANTS
    assert f"GCP_WIF_PROVIDER={POOL}/providers/github" in out
    assert f'"PUBLIC_BASE_URL": "https://assistant-{NUMBER}.europe-west1.run.app",' in out


def test_a_second_run_creates_nothing_and_applies_the_rest_again(gcloud: FakeGcloud) -> None:
    gcloud.exists(*EVERYTHING)
    gcloud.answers(f"billing projects describe {PROJECT}", "True")
    gcloud.answers("billing budgets list", "billingAccounts/0A0A0A/budgets/b1")
    setup(gcloud)
    assert created(gcloud) == []
    changes = gcloud.changes()
    assert not any(call.startswith("billing projects link") for call in changes)
    assert any(call.startswith("tasks queues update turns --location") for call in changes)
    assert any(" providers update-oidc github " in call for call in changes)
    assert grants(gcloud) == GRANTS


def test_a_dry_run_changes_nothing_and_shows_each_change(gcloud: FakeGcloud) -> None:
    out = setup(gcloud, DRY_RUN="1")
    assert gcloud.changes() == []
    assert f"would run: gcloud projects create {PROJECT}" in out
    # The project does not exist yet, so neither does its number.
    assert '"PUBLIC_BASE_URL": "https://assistant-PROJECT_NUMBER.europe-west1.run.app",' in out


def test_without_a_billing_account_nothing_is_touched(gcloud: FakeGcloud) -> None:
    done = gcloud.run(SCRIPT, PROJECT=PROJECT)
    assert done.returncode != 0
    assert "BILLING_ACCOUNT" in done.stderr
    assert gcloud.calls() == []
```

Create `tests/unit/test_push_config.py`:

```python
"""deploy/push-config.sh, against a stand-in gcloud and the real config
check: a config with problems is never uploaded."""

import json
from pathlib import Path

import pytest

from tests.fakes.gcloud import FakeGcloud

SCRIPT = str(Path(__file__).parents[2] / "deploy" / "push-config.sh")
EXAMPLE = Path(__file__).parents[2] / "deploy" / "config.example.json"
PROJECT = "rezerve-assistant"
NUMBER = "123456789012"


@pytest.fixture
def gcloud(tmp_path: Path) -> FakeGcloud:
    fake = FakeGcloud(tmp_path)
    fake.exists(f"projects describe {PROJECT}", "secrets versions describe latest")
    fake.answers(f"projects describe {PROJECT}", NUMBER)
    fake.answers("secrets versions describe latest", "4")
    fake.answers("secrets versions list assistant-config", "4\n3\n2\n1")
    return fake


def config(tmp_path: Path, **overrides: str) -> str:
    """The example as setup-gcp.sh's output and real secrets fill it in."""
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    values = {key: value.replace("REPLACE_ME", "x1") for key, value in example.items()} | {
        "PUBLIC_BASE_URL": f"https://assistant-{NUMBER}.europe-west1.run.app",
        "GCP_PROJECT": PROJECT,
        "TASKS_INVOKER_SA": f"assistant-tasks@{PROJECT}.iam.gserviceaccount.com",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values | overrides), encoding="utf-8")
    return str(path)


def test_the_config_becomes_the_running_services_and_old_versions_go(
    tmp_path: Path, gcloud: FakeGcloud
) -> None:
    gcloud.exists("run services describe assistant")
    path = config(tmp_path)
    done = gcloud.run(SCRIPT, path, PROJECT=PROJECT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "The config is version 4." in done.stdout
    assert gcloud.changes() == [
        f"secrets versions add assistant-config --data-file={path} --project={PROJECT} --quiet",
        "run services update assistant --region=europe-west1 "
        f"--project={PROJECT} --update-secrets=/secrets/config.json=assistant-config:4 --quiet",
        # Version 3 stays for a rollback.
        f"secrets versions destroy 2 --secret=assistant-config --project={PROJECT} --quiet",
        f"secrets versions destroy 1 --secret=assistant-config --project={PROJECT} --quiet",
    ]


def test_before_the_first_deploy_it_is_only_uploaded(tmp_path: Path, gcloud: FakeGcloud) -> None:
    done = gcloud.run(SCRIPT, config(tmp_path), PROJECT=PROJECT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not any(call.startswith("run services update") for call in gcloud.changes())


def test_a_config_with_problems_is_never_uploaded(tmp_path: Path, gcloud: FakeGcloud) -> None:
    # Copied from another project, its address would have Cloud Tasks call
    # back a service that is not this one.
    elsewhere = "https://assistant-999.europe-west1.run.app"
    done = gcloud.run(SCRIPT, config(tmp_path, PUBLIC_BASE_URL=elsewhere), PROJECT=PROJECT)
    assert done.returncode != 0
    assert (
        f"PUBLIC_BASE_URL: should be https://assistant-{NUMBER}.europe-west1.run.app" in done.stdout
    )
    assert gcloud.changes() == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/unit/test_setup_gcp.py tests/unit/test_push_config.py`
Expected: 7 failed. bash cannot open `deploy/setup-gcp.sh` or `deploy/push-config.sh`, and exits 127.

- [ ] **Step 3: Write the scripts and the cleanup policy**

Create `deploy/cleanup-policy.json`:

```json
[
  {
    "name": "delete-old-images",
    "action": { "type": "Delete" },
    "condition": { "tagState": "any" }
  },
  {
    "name": "keep-the-last-three",
    "action": { "type": "Keep" },
    "mostRecentVersions": { "keepCount": 3 }
  }
]
```

Create `deploy/setup-gcp.sh`:

```bash
#!/usr/bin/env bash
# Google Cloud for the assistant (spec §9), set up once. Running it again is
# safe: what exists is kept, and the queue rates, cleanup policy and grants
# are applied again.
#
#   PROJECT=<project id> BILLING_ACCOUNT=<id from `gcloud billing accounts list`> \
#     deploy/setup-gcp.sh
#
# DRY_RUN=1 shows each change and makes none. GITHUB_REPO (default
# Olbioss/rezerve-assistant) is the repository whose main branch deploys.
set -euo pipefail

: "${PROJECT:?Set PROJECT to the Google Cloud project id.}"
: "${BILLING_ACCOUNT:?Set BILLING_ACCOUNT; gcloud billing accounts list shows it.}"
REGION="${REGION:-europe-west1}"
GITHUB_REPO="${GITHUB_REPO:-Olbioss/rezerve-assistant}"
HERE="$(cd "$(dirname "$0")" && pwd)"

SERVICE=assistant
REPOSITORY=assistant
SECRET=assistant-config
RUNTIME="assistant-runtime@$PROJECT.iam.gserviceaccount.com"
TASKS="assistant-tasks@$PROJECT.iam.gserviceaccount.com"
DEPLOYER="assistant-deployer@$PROJECT.iam.gserviceaccount.com"

# A change: made, or with DRY_RUN=1 only shown.
change() {
  if [[ "${DRY_RUN:-}" == 1 ]]; then
    echo "would run: gcloud $*"
  else
    echo "+ gcloud $*"
    gcloud "$@" --quiet --format=none
  fi
}

# Whether a describe finds it.
exists() {
  gcloud "$@" --quiet >/dev/null 2>&1
}

# The project, on the billing account.
if ! exists projects describe "$PROJECT"; then
  change projects create "$PROJECT"
fi
billing="$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null || true)"
if [[ "$billing" != True ]]; then
  change billing projects link "$PROJECT" --billing-account="$BILLING_ACCOUNT"
fi
# A new project's number is not known until it exists.
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)' 2>/dev/null || echo PROJECT_NUMBER)"

change services enable run.googleapis.com cloudtasks.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com \
  billingbudgets.googleapis.com --project="$PROJECT"

# The images, the last three kept: Artifact Registry's free 0.5 GB. Scanning
# would be billed.
if ! exists artifacts repositories describe "$REPOSITORY" --location="$REGION" --project="$PROJECT"; then
  change artifacts repositories create "$REPOSITORY" --location="$REGION" \
    --repository-format=docker --disable-vulnerability-scanning --project="$PROJECT"
fi
change artifacts repositories set-cleanup-policies "$REPOSITORY" --location="$REGION" \
  --policy="$HERE/cleanup-policy.json" --no-dry-run --project="$PROJECT"

# The queues, at spec §9's rates; reminders keep Cloud Tasks' defaults.
if exists tasks queues describe turns --location="$REGION" --project="$PROJECT"; then
  verb=update
else
  verb=create
fi
change tasks queues "$verb" turns --location="$REGION" --max-dispatches-per-second=2 \
  --max-concurrent-dispatches=3 --max-attempts=6 --min-backoff=2s --max-backoff=60s \
  --project="$PROJECT"
if ! exists tasks queues describe reminders --location="$REGION" --project="$PROJECT"; then
  change tasks queues create reminders --location="$REGION" --project="$PROJECT"
fi

account() {
  if ! exists iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" --project="$PROJECT"; then
    change iam service-accounts create "$1" --display-name="$2" --project="$PROJECT"
  fi
}
account assistant-runtime "The assistant service"
account assistant-tasks "Cloud Tasks calling the assistant back"
account assistant-deployer "GitHub Actions deploying the assistant"

# Every setting, as one JSON object; deploy/push-config.sh adds its versions.
if ! exists secrets describe "$SECRET" --project="$PROJECT"; then
  change secrets create "$SECRET" --replication-policy=user-managed --locations="$REGION" \
    --project="$PROJECT"
fi

# The service queues turns and reminders, each call back signed as the tasks
# account, and reads its config.
change projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME" \
  --role=roles/cloudtasks.enqueuer --condition=None
change iam service-accounts add-iam-policy-binding "$TASKS" --member="serviceAccount:$RUNTIME" \
  --role=roles/iam.serviceAccountUser --project="$PROJECT"
change secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$RUNTIME" \
  --role=roles/secretmanager.secretAccessor --project="$PROJECT"
# Cloud Tasks mints those tokens itself: its service agent, made now if Google
# has not made it yet, acts as the tasks account.
change beta services identity create --service=cloudtasks.googleapis.com --project="$PROJECT"
change iam service-accounts add-iam-policy-binding "$TASKS" \
  --member="serviceAccount:service-$NUMBER@gcp-sa-cloudtasks.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountUser --project="$PROJECT"
# The deployer: the service, the migration job and the service's public access;
# both run as the runtime account; images pushed; the config's version numbers
# read (never its values).
change projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$DEPLOYER" \
  --role=roles/run.admin --condition=None
change iam service-accounts add-iam-policy-binding "$RUNTIME" --member="serviceAccount:$DEPLOYER" \
  --role=roles/iam.serviceAccountUser --project="$PROJECT"
change artifacts repositories add-iam-policy-binding "$REPOSITORY" --location="$REGION" \
  --member="serviceAccount:$DEPLOYER" --role=roles/artifactregistry.writer --project="$PROJECT"
change secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$DEPLOYER" \
  --role=roles/secretmanager.viewer --project="$PROJECT"

# GitHub Actions deploys as the deployer, with no key: only from this
# repository's main branch.
POOL="projects/$NUMBER/locations/global/workloadIdentityPools/github"
if ! exists iam workload-identity-pools describe github --location=global --project="$PROJECT"; then
  change iam workload-identity-pools create github --location=global \
    --display-name="GitHub Actions" --project="$PROJECT"
fi
if exists iam workload-identity-pools providers describe github --workload-identity-pool=github \
  --location=global --project="$PROJECT"; then
  verb=update-oidc
else
  verb=create-oidc
fi
change iam workload-identity-pools providers "$verb" github --workload-identity-pool=github \
  --location=global --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
  --attribute-condition="assertion.repository == '$GITHUB_REPO' && assertion.ref == 'refs/heads/main'" \
  --project="$PROJECT"
change iam service-accounts add-iam-policy-binding "$DEPLOYER" \
  --member="principalSet://iam.googleapis.com/$POOL/attribute.repository/$GITHUB_REPO" \
  --role=roles/iam.workloadIdentityUser --project="$PROJECT"

# An alert at half and all of one unit of the billing account's currency:
# the service is meant to cost nothing.
budgets="$(gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" \
  --filter="displayName=$PROJECT" --format='value(name)' --billing-project="$PROJECT" 2>/dev/null || true)"
if [[ -z "$budgets" ]]; then
  change billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="$PROJECT" \
    --budget-amount=1 --threshold-rule=percent=0.5 --threshold-rule=percent=1.0 \
    --filter-projects="projects/$PROJECT" --billing-project="$PROJECT"
fi

cat <<DONE

Set up. GitHub, Settings > Secrets and variables > Actions > Variables:
  GCP_PROJECT=$PROJECT
  GCP_WIF_PROVIDER=$POOL/providers/github
deploy/config.json:
  "PUBLIC_BASE_URL": "https://$SERVICE-$NUMBER.$REGION.run.app",
  "GCP_PROJECT": "$PROJECT",
  "TASKS_INVOKER_SA": "$TASKS",
DONE
```

Create `deploy/push-config.sh`:

```bash
#!/usr/bin/env bash
# The production config, checked, then uploaded as the secret's new version;
# a running service moves to it at once. The version before stays enabled for
# a rollback, older ones are destroyed: Secret Manager's free tier is six
# versions.
#
#   PROJECT=<project id> deploy/push-config.sh [deploy/config.json]
set -euo pipefail

: "${PROJECT:?Set PROJECT to the Google Cloud project id.}"
REGION="${REGION:-europe-west1}"
FILE="${1:-deploy/config.json}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE=assistant
SECRET=assistant-config

NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
uv run --project "$ROOT" assistant config check "$FILE" \
  --expect="PUBLIC_BASE_URL=https://$SERVICE-$NUMBER.$REGION.run.app" \
  --expect="GCP_PROJECT=$PROJECT" \
  --expect="GCP_REGION=$REGION" \
  --expect="QUEUE_BACKEND=cloud_tasks" \
  --expect="TASKS_INVOKER_SA=assistant-tasks@$PROJECT.iam.gserviceaccount.com"

gcloud secrets versions add "$SECRET" --data-file="$FILE" --project="$PROJECT" --quiet
VERSION="$(gcloud secrets versions describe latest --secret="$SECRET" --project="$PROJECT" \
  --format='value(name.basename())')"
echo "The config is version $VERSION."

# Before the first deploy there is no service yet: the deploy pins the newest.
if gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud run services update "$SERVICE" --region="$REGION" --project="$PROJECT" \
    --update-secrets="/secrets/config.json=$SECRET:$VERSION" --quiet
fi

# Newest first: keep two.
gcloud secrets versions list "$SECRET" --project="$PROJECT" --filter="state=ENABLED" \
  --sort-by=~createTime --format='value(name.basename())' | tail -n +3 |
  while read -r old; do
    gcloud secrets versions destroy "$old" --secret="$SECRET" --project="$PROJECT" --quiet
  done
```

```bash
chmod +x deploy/setup-gcp.sh deploy/push-config.sh
```

Modify `.github/workflows/ci.yml`:

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -98,3 +98,4 @@ jobs:
       - uses: actions/checkout@v7
       # With shellcheck on each step's script.
       - run: docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -color
+      - run: shellcheck deploy/*.sh
```

- [ ] **Step 4: Run them, then lint, then parse every gcloud command offline**

Run: `uv run pytest -q tests/unit/test_setup_gcp.py tests/unit/test_push_config.py`
Expected: 7 passed. Then:

```bash
docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable deploy/setup-gcp.sh deploy/push-config.sh
/bin/bash -n deploy/setup-gcp.sh && /bin/bash -n deploy/push-config.sh && echo "bash 3.2 parses both"
```

Expected: shellcheck prints nothing, and macOS's bash 3.2 parses both.

gcloud refuses an unknown flag before it looks for credentials. So, in its image with no account, a command whose flags are right fails only for want of an account. Save this as `flags.sh` in a scratch directory:

```bash
set -u
P=rezerve-assistant; R=europe-west1
run() {
  out="$(gcloud "$@" --quiet 2>&1)"
  if echo "$out" | grep -qiE "unrecognized arguments|invalid choice|must be specified|Bad value"; then
    echo "BAD: gcloud $*"; echo "$out" | head -3
  else
    echo "ok: $1 $2 $3"
  fi
}
run projects describe $P --format='value(projectNumber)'
run billing projects link $P --billing-account=0A0A0A-0A0A0A-0A0A0A
run artifacts repositories create assistant --location=$R --repository-format=docker --disable-vulnerability-scanning --project=$P
echo '[]' > /tmp/p.json   # inside the container
run artifacts repositories set-cleanup-policies assistant --location=$R --policy=/tmp/p.json --no-dry-run --project=$P
run tasks queues create turns --location=$R --max-dispatches-per-second=2 --max-concurrent-dispatches=3 --max-attempts=6 --min-backoff=2s --max-backoff=60s --project=$P
run tasks queues update turns --location=$R --max-dispatches-per-second=2 --max-concurrent-dispatches=3 --max-attempts=6 --min-backoff=2s --max-backoff=60s --project=$P
run secrets create assistant-config --replication-policy=user-managed --locations=$R --project=$P
run projects add-iam-policy-binding $P --member=serviceAccount:a@b.iam.gserviceaccount.com --role=roles/run.admin --condition=None
run beta services identity create --service=cloudtasks.googleapis.com --project=$P
run iam workload-identity-pools providers create-oidc github --workload-identity-pool=github --location=global --issuer-uri=https://token.actions.githubusercontent.com --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref "--attribute-condition=assertion.repository == 'x/y'" --project=$P
run billing budgets create --billing-account=0A0A0A-0A0A0A-0A0A0A --display-name=$P --budget-amount=1 --threshold-rule=percent=0.5 --threshold-rule=percent=1.0 --filter-projects=projects/$P --billing-project=$P
run secrets versions describe latest --secret=assistant-config --project=$P --format='value(name.basename())'
run run services update assistant --region=$R --project=$P --update-secrets=/secrets/config.json=assistant-config:4
run secrets versions list assistant-config --project=$P --filter=state=ENABLED --sort-by=~createTime --format='value(name.basename())'
run secrets versions destroy 2 --secret=assistant-config --project=$P
```

Run it from that directory:

```bash
docker run --rm -v "$PWD/flags.sh:/flags.sh" gcr.io/google.com/cloudsdktool/google-cloud-cli:slim bash /flags.sh
```

Expected: every line `ok:`. To see the check work, misspell one flag, say `--max-attempt=6`: that line turns `BAD`, with gcloud's "did you mean '--max-attempts'". Then the four checks: 555 passed; ruff and pyright clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(deploy): setup-gcp.sh sets up the project once, push-config.sh checks and uploads the config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9: Each `main` deployed to Cloud Run, migrating first

Spec §9's continuous deployment: test → build → push → migrate → `gcloud run deploy`. It is a `deploy` job in `ci.yml`. It needs the test, image and workflow jobs, runs on a push to `main` or "Run workflow" on `main`, and is skipped until `vars.GCP_PROJECT` is set. In order:
- **Keyless sign-in:** GitHub's OIDC token is exchanged for the deployer's, so no key is stored anywhere.
- **The image,** tagged with the commit and pushed to Artifact Registry.
- **The config pinned:** the newest version of `assistant-config`, resolved to its number, so the revision keeps the config it started with.
- **The migration:** `alembic upgrade head` as the Cloud Run job `assistant-migrate`, with the same image and secret, run to completion before the deploy.
- **The deploy, per spec §9:**
  - the runtime account;
  - the secret mounted at `/secrets/config.json`, and `CONFIG_FILE` naming it;
  - public, since the widget and Telegram call it;
  - CPU billed per request;
  - 1 CPU and 512Mi;
  - concurrency 20;
  - 0 to 2 instances.
- **The check:** `/health` on the new revision.

The README gains the production notes this plan adds, and how to deploy.

**Files:**
- Modify: `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: Task 8's resource names; Task 7's jobs; the GitHub variables `GCP_PROJECT` and `GCP_WIF_PROVIDER`.
- Produces: the service `assistant` in europe-west1, at `https://assistant-<project number>.europe-west1.run.app`; the job `assistant-migrate`.

- [ ] **Step 1: Write the job and the trigger**

Modify `.github/workflows/ci.yml`:

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -7,6 +7,9 @@ on:
   push:
     branches: [main]
   pull_request:
+  # "Run workflow" on main deploys it again, with no new commit: the first
+  # deploy, once the project is set up, and a redeploy after a config change.
+  workflow_dispatch:
 
 permissions:
   contents: read
@@ -99,3 +102,74 @@ jobs:
       # With shellcheck on each step's script.
       - run: docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -color
       - run: shellcheck deploy/*.sh
+
+  # Each push to main, and each run started by hand on main, once
+  # deploy/setup-gcp.sh has set the project up and its two variables are in
+  # the repository's settings.
+  deploy:
+    if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main' && vars.GCP_PROJECT != ''
+    needs: [test, image, workflows]
+    runs-on: ubuntu-latest
+    # One at a time, in push order.
+    concurrency:
+      group: deploy
+      cancel-in-progress: false
+    permissions:
+      contents: read
+      id-token: write
+    env:
+      PROJECT: ${{ vars.GCP_PROJECT }}
+      REGION: europe-west1
+      IMAGE: europe-west1-docker.pkg.dev/${{ vars.GCP_PROJECT }}/assistant/assistant:${{ github.sha }}
+      RUNTIME: assistant-runtime@${{ vars.GCP_PROJECT }}.iam.gserviceaccount.com
+    steps:
+      - uses: actions/checkout@v7
+      # GitHub's OIDC token, exchanged for the deployer's: no key is stored.
+      - id: auth
+        uses: google-github-actions/auth@v3
+        with:
+          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
+          service_account: assistant-deployer@${{ vars.GCP_PROJECT }}.iam.gserviceaccount.com
+          token_format: access_token
+      - uses: google-github-actions/setup-gcloud@v3
+      - uses: docker/login-action@v4
+        with:
+          registry: europe-west1-docker.pkg.dev
+          username: oauth2accesstoken
+          password: ${{ steps.auth.outputs.access_token }}
+      - uses: docker/setup-buildx-action@v4
+      - uses: docker/build-push-action@v7
+        with:
+          context: .
+          push: true
+          tags: ${{ env.IMAGE }}
+          cache-from: type=gha
+      # A revision keeps the config version it started with; a newer one
+      # arrives with deploy/push-config.sh or the next deploy.
+      - name: Pin the config's newest version
+        run: |
+          version="$(gcloud secrets versions describe latest --secret=assistant-config \
+            --project="$PROJECT" --format='value(name.basename())')"
+          echo "CONFIG=assistant-config:$version" >> "$GITHUB_ENV"
+      - name: Migrate the database
+        run: |
+          gcloud run jobs deploy assistant-migrate --image="$IMAGE" --region="$REGION" \
+            --project="$PROJECT" --service-account="$RUNTIME" \
+            --set-secrets="/secrets/config.json=$CONFIG" \
+            --set-env-vars=CONFIG_FILE=/secrets/config.json \
+            --command=alembic --args=upgrade,head --max-retries=0 --task-timeout=300s \
+            --execute-now --wait --quiet
+      # Spec §9: billed per request, 0 to 2 instances, 20 requests each.
+      - name: Deploy the service
+        run: |
+          gcloud run deploy assistant --image="$IMAGE" --region="$REGION" --project="$PROJECT" \
+            --service-account="$RUNTIME" \
+            --set-secrets="/secrets/config.json=$CONFIG" \
+            --set-env-vars=CONFIG_FILE=/secrets/config.json \
+            --allow-unauthenticated --cpu-throttling --cpu=1 --memory=512Mi --concurrency=20 \
+            --min-instances=0 --max-instances=2 --timeout=300 --quiet
+      - name: Ask it for /health
+        run: |
+          url="$(gcloud run services describe assistant --region="$REGION" --project="$PROJECT" \
+            --format='value(status.url)')"
+          curl -fsS --retry 5 --retry-all-errors "$url/health"
```

- [ ] **Step 2: Lint it, and parse its gcloud commands offline**

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -color
```

Expected: nothing, exit 0. Then append the deploy job's commands to Task 8's `flags.sh`, and run it as there:

```bash
I=europe-west1-docker.pkg.dev/p/assistant/assistant:abc
run run jobs deploy assistant-migrate --image=$I --region=$R --project=$P --service-account=r@p.iam.gserviceaccount.com --set-secrets=/secrets/config.json=assistant-config:4 --set-env-vars=CONFIG_FILE=/secrets/config.json --command=alembic --args=upgrade,head --max-retries=0 --task-timeout=300s --execute-now --wait
run run deploy assistant --image=$I --region=$R --project=$P --service-account=r@p.iam.gserviceaccount.com --set-secrets=/secrets/config.json=assistant-config:4 --set-env-vars=CONFIG_FILE=/secrets/config.json --allow-unauthenticated --cpu-throttling --cpu=1 --memory=512Mi --concurrency=20 --min-instances=0 --max-instances=2 --timeout=300
run run services describe assistant --region=$R --project=$P --format='value(status.url)'
```

Expected: every line `ok:`.

- [ ] **Step 3: The README**

Modify `README.md`:

````diff
--- a/README.md
+++ b/README.md
@@ -97,7 +97,9 @@ uv run ruff check && uv run ruff format --check && uv run pyright
 
 The agent tests use scripted models and an in-process fake Rezerve: no
 network, no quota. CI (`.github/workflows/ci.yml`) runs the same checks
-against a Postgres service container, and builds the image.
+against a Postgres service container. It also builds the image, migrates an
+empty database with it and asks it for `/health`, and lints the workflows and
+the deploy scripts. A push to `main` deploys.
 
 ## In production
 
@@ -141,7 +143,65 @@ The same service, with what a public demo needs:
   as its user, the model, tokens and outcome. Its id is kept on `agent_runs`,
   past Langfuse's 30 days. What customers wrote stays out unless
   `TRACE_CONTENT=true`, and httpx is not traced: Telegram's URLs hold the
-  bot token.
+  bot token. The widget's requests are not traced at all, and each call back
+  from Cloud Tasks sends its spans before it answers: Cloud Run gives an
+  instance CPU only while it answers.
+- **Logs.** One JSON object a line, which Cloud Logging reads with its
+  severity. Each instance logs once how many `X-Forwarded-For` entries a
+  widget post carried, never the addresses: what `TRUSTED_PROXY_HOPS` should
+  be.
+
+### Deploying
+
+On Google Cloud, in europe-west1 (spec §9). Once, with
+[gcloud](https://cloud.google.com/sdk/docs/install) signed in
+(`gcloud auth login`, and `gcloud components install beta`):
+
+```bash
+PROJECT=<project id> BILLING_ACCOUNT=<from gcloud billing accounts list> deploy/setup-gcp.sh
+```
+
+It creates the project if it has to, then everything around the service:
+- Artifact Registry, keeping the last three images;
+- the `turns` and `reminders` queues;
+- three service accounts: the service, Cloud Tasks' calls back, GitHub's deploys;
+- the config secret;
+- keyless access for GitHub Actions, from `main` only;
+- a budget alert at one unit of the billing account's currency.
+
+`DRY_RUN=1` shows each change and makes none. Running it again changes only
+what drifted. It ends with the two repository variables GitHub needs
+(`GCP_PROJECT`, `GCP_WIF_PROVIDER`) and three values for the config.
+
+The config is `deploy/config.json`, git-ignored: copy
+`deploy/config.example.json` and replace each `REPLACE_ME`. `DATABASE_URL` is
+Neon's direct endpoint, not its `-pooler` one, written
+`postgresql+asyncpg://…?ssl=require`: asyncpg takes neither `sslmode` nor
+`channel_binding`. Then:
+
+```bash
+PROJECT=<project id> deploy/push-config.sh
+```
+
+It checks the file (`uv run assistant config check deploy/config.json`, which
+names each problem by its key, never its value), uploads it as the secret's
+new version, and moves a running service to it. The version before stays
+for a rollback.
+
+From then on each push to `main` deploys, once its checks pass: the image,
+tagged with the commit; `alembic upgrade head` as a Cloud Run job; the
+service, at 0 to 2 instances billed per request; a `/health` check. Each
+revision keeps the config version it started with. The first deploy needs no
+commit: Actions > CI > Run workflow, on `main`.
+
+Last, Telegram:
+
+```bash
+uv run assistant telegram webhook deploy/config.json
+```
+
+It points the bot at the service, with the webhook's secret, and prints
+Telegram's view: how many updates wait, and its last delivery error.
 
 ## Evals
 
````

- [ ] **Step 4: Run the four checks**

Expected: 555 passed; ruff and pyright clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "ci: each push to main deploys to Cloud Run, migrating first; the README says how to set it up

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 10: Rezerve tells the assistant a kapora was paid

Spec §6.5. When a kapora is paid for a booking the assistant made, Rezerve sends `booking.confirmed` to the assistant's `/events/rezerve`, and the customer hears it in their chat. 4a built the receiver, and fixed the contract:
- the body `{type, booking_id, business_slug, customer_ref, service_name, local_label}`;
- `X-Rezerve-Timestamp` in Unix seconds;
- `X-Rezerve-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw body>">` under `ASSISTANT_EVENT_SECRET`;
- a five-minute window.

`confirmPaidBooking` schedules the send with Next's `after()`, so the customer's redirect to the confirmation page never waits on a cold-starting assistant. Only a booking whose `source` is `assistant` is told. The send is best effort: one attempt, a 10 s timeout, any failure logged and swallowed. With no `ASSISTANT_URL` or `ASSISTANT_EVENT_SECRET`, nothing is sent.

One HMAC test vector is pinned in both repositories, so a change on either side fails a test, not production.

**Files:**
- Agent, create: `tests/unit/test_event_signature.py`
- Rezerve, create: `lib/assistant/events.ts`, `lib/assistant/events.test.ts`
- Rezerve, modify: `lib/payments/handle-payment-result.ts`, `lib/payments/handle-payment-result.integration.test.ts`, `README.md`

**Interfaces:**
- Consumes: the agent's `assistant.events.signature(secret, timestamp, body)` (4a); Rezerve's `localLabel(instant, timezone)`, `confirmPaidBooking(bookingId, token, paymentTransactionId?, customerIp?)`.
- Produces, in Rezerve's `lib/assistant/events.ts`:
  - `type BookingConfirmedEvent`;
  - `eventSignature(secret: string, timestamp: string, body: string): string`;
  - `notifyAssistant(event: BookingConfirmedEvent): Promise<void>`.

- [ ] **Step 1: The agent's side of the vector** (in `~/repos/deploys/rezerve-assistant`)

Create `tests/unit/test_event_signature.py`:

```python
from assistant.events import signature


def test_the_signature_rezerve_computes_too() -> None:
    # Rezerve's lib/assistant/events.test.ts pins the same value: a change on
    # one side fails both suites, not production.
    body = b'{"type":"booking.confirmed","booking_id":"b1"}'
    assert signature("event-secret", "1767225600", body) == (
        "sha256=c8ecbff0f5205233624b744225a4aa6d3f3c9a1dc7dda4377ab39c8ffe48de47"
    )
```

Run: `uv run pytest -q tests/unit/test_event_signature.py`
Expected: 1 passed, at once: it pins what the agent already signs. Then run the four checks (556 passed), and commit:

```bash
git add tests/unit/test_event_signature.py
git commit -m "test(events): the signature Rezerve computes, pinned on both sides

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Rezerve's failing tests** (in `~/repos/deploys/rezerve`, from here on)

Create `lib/assistant/events.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type BookingConfirmedEvent,
  eventSignature,
  notifyAssistant,
} from "./events";

const EVENT: BookingConfirmedEvent = {
  type: "booking.confirmed",
  booking_id: "b1",
  business_slug: "demo",
  customer_ref: "telegram:abcdefghijklmnopqrstuvwxyz",
  service_name: "Saç Kesimi",
  local_label: "Cumartesi 17 Ekim 15:30",
};

describe("eventSignature", () => {
  it("is the one the assistant checks", () => {
    // rezerve-assistant's tests/unit/test_event_signature.py pins the same
    // value: a change on one side fails both suites, not production.
    const body = JSON.stringify({
      type: "booking.confirmed",
      booking_id: "b1",
    });
    expect(eventSignature("event-secret", "1767225600", body)).toBe(
      "sha256=c8ecbff0f5205233624b744225a4aa6d3f3c9a1dc7dda4377ab39c8ffe48de47"
    );
  });
});

describe("notifyAssistant", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock
      .mockReset()
      .mockResolvedValue(new Response(null, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("ASSISTANT_URL", "https://assistant.example");
    vi.stubEnv("ASSISTANT_EVENT_SECRET", "event-secret");
    vi.useFakeTimers({
      now: new Date("2026-01-01T00:00:00Z"),
      toFake: ["Date"],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("posts the event, signed over its timestamp and body", async () => {
    await notifyAssistant(EVENT);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(String(url)).toBe("https://assistant.example/events/rezerve");
    expect(init.method).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("x-rezerve-timestamp")).toBe("1767225600");
    expect(headers.get("x-rezerve-signature")).toBe(
      eventSignature("event-secret", "1767225600", String(init.body))
    );
    expect(JSON.parse(String(init.body))).toEqual(EVENT);
  });

  it.each([["ASSISTANT_URL"], ["ASSISTANT_EVENT_SECRET"]])(
    "sends nothing without %s",
    async (name) => {
      vi.stubEnv(name, "");
      await notifyAssistant(EVENT);
      expect(fetchMock).not.toHaveBeenCalled();
    }
  );

  it("logs a refusal and carries on", async () => {
    // Best effort, like the assistant's side: the confirmation page and email
    // have told the customer already.
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    await expect(notifyAssistant(EVENT)).resolves.toBeUndefined();
    expect(logged).toHaveBeenCalledWith(
      "Assistant event booking.confirmed for booking b1: HTTP 401"
    );
  });

  it("logs an assistant that cannot be reached and carries on", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    await expect(notifyAssistant(EVENT)).resolves.toBeUndefined();
    expect(logged).toHaveBeenCalledTimes(1);
  });
});
```

In the payment tests, `after()` is stood in for, so each test runs what it scheduled:

Modify `lib/payments/handle-payment-result.integration.test.ts`:

```diff
--- a/lib/payments/handle-payment-result.integration.test.ts
+++ b/lib/payments/handle-payment-result.integration.test.ts
@@ -5,6 +5,7 @@
 import { eq } from "drizzle-orm";
 import {
   afterAll,
+  afterEach,
   beforeAll,
   beforeEach,
   describe,
@@ -24,6 +25,18 @@ const { approveMock, refundMock } = vi.hoisted(() => ({
   approveMock: vi.fn(),
   refundMock: vi.fn(),
 }));
+
+// What confirmPaidBooking leaves for after the response: Next.js runs it once
+// the redirect is sent, and here the test does.
+const { scheduled } = vi.hoisted(() => ({
+  scheduled: [] as (() => unknown)[],
+}));
+vi.mock("next/server", async (importOriginal) => ({
+  ...(await importOriginal<typeof import("next/server")>()),
+  after: (task: () => unknown) => {
+    scheduled.push(task);
+  },
+}));
 vi.mock("@/lib/payments", () => ({
   getPaymentProvider: () => ({
     approveTransaction: approveMock,
@@ -35,6 +48,7 @@ const { confirmPaidBooking, cancelFailedPayment } = await import(
   "./handle-payment-result"
 );
 const { db } = await import("@/lib/db");
+const { localLabel } = await import("@/lib/assistant/format");
 const { organization } = await import("@/lib/db/schema/auth-schema");
 const { bookings } = await import("@/lib/db/schema/booking-schema");
 const { businessProfiles } = await import("@/lib/db/schema/business-schema");
@@ -77,6 +91,7 @@ beforeAll(async () => {
 });
 
 beforeEach(async () => {
+  scheduled.length = 0;
   sendConfirmed.mockClear();
   sendCancelled.mockClear();
   await db.delete(bookings).where(eq(bookings.organizationId, ORG_ID));
@@ -138,6 +153,63 @@ describe("handle-payment-result (integration)", () => {
   });
 });
 
+describe("confirmPaidBooking — telling the assistant", () => {
+  const REF = "telegram:abcdefghijklmnopqrstuvwxyz";
+  const fetchMock = vi.fn();
+
+  beforeEach(() => {
+    fetchMock
+      .mockReset()
+      .mockResolvedValue(new Response(null, { status: 202 }));
+    vi.stubGlobal("fetch", fetchMock);
+    vi.stubEnv("ASSISTANT_URL", "https://assistant.example");
+    vi.stubEnv("ASSISTANT_EVENT_SECRET", "event-secret");
+  });
+
+  afterEach(() => {
+    vi.unstubAllGlobals();
+    vi.unstubAllEnvs();
+  });
+
+  it("tells it of a kapora paid for its booking, after the response", async () => {
+    // The customer paid in their browser; their chat hears it too (spec §6.5).
+    await db
+      .update(bookings)
+      .set({ source: "assistant", customerRef: REF })
+      .where(eq(bookings.id, bookingId));
+
+    expect(await confirmPaidBooking(bookingId, TOKEN)).toBe("confirmed");
+    // The redirect to the confirmation page never waits on the assistant.
+    expect(fetchMock).not.toHaveBeenCalled();
+    expect(scheduled).toHaveLength(1);
+
+    await scheduled[0]();
+    expect(fetchMock).toHaveBeenCalledTimes(1);
+    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
+    expect(String(url)).toBe("https://assistant.example/events/rezerve");
+    const row = await db.query.bookings.findFirst({
+      where: eq(bookings.id, bookingId),
+    });
+    expect(JSON.parse(String(init.body))).toEqual({
+      type: "booking.confirmed",
+      booking_id: bookingId,
+      business_slug: "itest-payment",
+      customer_ref: REF,
+      service_name: "Kaporali Bakim",
+      local_label: localLabel(row!.startsAt, "UTC"),
+    });
+
+    // A replayed callback confirms nothing, so it tells nothing.
+    expect(await confirmPaidBooking(bookingId, TOKEN)).toBe("noop");
+    expect(scheduled).toHaveLength(1);
+  });
+
+  it("leaves it out of a booking made on the booking page", async () => {
+    expect(await confirmPaidBooking(bookingId, TOKEN)).toBe("confirmed");
+    expect(scheduled).toEqual([]);
+  });
+});
+
 describe("confirmPaidBooking — releasing the kapora", () => {
   const TX = "39798795";
 
```

- [ ] **Step 3: Run them to see them fail**

Run: `env $REZERVE_TEST_ENV bunx vitest run lib/assistant/events.test.ts lib/payments/handle-payment-result.integration.test.ts`
Expected: `events.test.ts` fails to load `./events`. The payment test `tells it of a kapora paid for its booking, after the response` fails with `expected [] to have a length of 1`.

- [ ] **Step 4: Implement**

Create `lib/assistant/events.ts`:

```typescript
import { createHmac } from "node:crypto";

/**
 * Events Rezerve sends the chat assistant (spec §6.5), to `POST
 * /events/rezerve`. Each is signed with HMAC-SHA256 over "<timestamp>.<body>"
 * under ASSISTANT_EVENT_SECRET, and the assistant refuses one outside five
 * minutes of its timestamp.
 *
 * Best effort: one attempt, a failure logged and swallowed. The confirmation
 * page and email have already told the customer.
 */

/** A kapora paid: Rezerve confirmed a booking the assistant made. */
export type BookingConfirmedEvent = {
  type: "booking.confirmed";
  booking_id: string;
  business_slug: string;
  customer_ref: string;
  service_name: string;
  /** "Cumartesi 17 Ekim 15:30", in the business's timezone (localLabel). */
  local_label: string;
};

/** What goes in X-Rezerve-Signature. */
export function eventSignature(
  secret: string,
  timestamp: string,
  body: string
): string {
  const digest = createHmac("sha256", secret)
    .update(`${timestamp}.${body}`)
    .digest("hex");
  return `sha256=${digest}`;
}

export async function notifyAssistant(
  event: BookingConfirmedEvent
): Promise<void> {
  const url = process.env.ASSISTANT_URL;
  const secret = process.env.ASSISTANT_EVENT_SECRET;
  if (!url || !secret) return;

  const body = JSON.stringify(event);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const what = `Assistant event ${event.type} for booking ${event.booking_id}`;
  try {
    const response = await fetch(new URL("/events/rezerve", url), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-rezerve-timestamp": timestamp,
        "x-rezerve-signature": eventSignature(secret, timestamp, body),
      },
      body,
      // A cold start on Cloud Run takes seconds; the event is not worth more.
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) console.error(`${what}: HTTP ${response.status}`);
  } catch (err) {
    console.error(`${what}: not delivered`, err);
  }
}
```

Modify `lib/payments/handle-payment-result.ts`:

```diff
--- a/lib/payments/handle-payment-result.ts
+++ b/lib/payments/handle-payment-result.ts
@@ -1,10 +1,18 @@
 import { and, eq, isNull, sql } from "drizzle-orm";
+import { after } from "next/server";
+import { notifyAssistant } from "@/lib/assistant/events";
+import { localLabel } from "@/lib/assistant/format";
 import { refundDeposit } from "@/lib/booking/refund-deposit";
 import { db } from "@/lib/db";
+import { organization } from "@/lib/db/schema/auth-schema";
 import { bookings } from "@/lib/db/schema/booking-schema";
+import { businessProfiles } from "@/lib/db/schema/business-schema";
+import { services } from "@/lib/db/schema/service-schema";
 import { sendBookingConfirmedEmails } from "@/lib/email/booking-notifications";
 import { getPaymentProvider } from "@/lib/payments";
 
+type Booking = typeof bookings.$inferSelect;
+
 /**
  * Payment-callback business logic, separated from the route for testing.
  *
@@ -80,9 +88,39 @@ export async function confirmPaidBooking(
   }
 
   await sendBookingConfirmedEmails(confirmed);
+  if (confirmed.source === "assistant") {
+    // Once the redirect is sent: a cold-starting assistant never holds up the
+    // customer's confirmation page.
+    after(() => tellAssistant(confirmed));
+  }
   return "confirmed";
 }
 
+/**
+ * The customer who booked in a chat hears there that their kapora arrived
+ * (spec §6.5), as well as on the confirmation page and by email.
+ */
+async function tellAssistant(booking: Booking): Promise<void> {
+  const [org, profile, service] = await Promise.all([
+    db.query.organization.findFirst({
+      where: eq(organization.id, booking.organizationId),
+    }),
+    db.query.businessProfiles.findFirst({
+      where: eq(businessProfiles.organizationId, booking.organizationId),
+    }),
+    db.query.services.findFirst({ where: eq(services.id, booking.serviceId) }),
+  ]);
+  if (!org || !profile || !service || !booking.customerRef) return;
+  await notifyAssistant({
+    type: "booking.confirmed",
+    booking_id: booking.id,
+    business_slug: org.slug,
+    customer_ref: booking.customerRef,
+    service_name: service.name,
+    local_label: localLabel(booking.startsAt, profile.timezone),
+  });
+}
+
 /** Failed/abandoned payment: release the hold. No email for a failed attempt. */
 export async function cancelFailedPayment(
   bookingId: string,
```

- [ ] **Step 5: Run the three checks, and format**

```bash
bunx biome check --write lib/assistant/events.ts lib/assistant/events.test.ts \
  lib/payments/handle-payment-result.ts lib/payments/handle-payment-result.integration.test.ts
bun run typecheck && bun run check && env $REZERVE_TEST_ENV bun run test
```

Expected: 434 passed; Biome's one old warning and no new one.

- [ ] **Step 6: The README, and commit**

Modify `README.md`:

```diff
--- a/README.md
+++ b/README.md
@@ -412,6 +412,15 @@ a random uuid kept in localStorage, is the conversation's only credential. It
 posts a message, then polls every second while an answer is coming, for at
 most a minute, and a kapora link arrives as a button.
 
+**The kapora event.** When a kapora is paid for a booking the assistant made,
+Rezerve tells the assistant, which tells the customer in their chat:
+`booking.confirmed`, posted to its `/events/rezerve` once the confirmation
+redirect is sent (`lib/assistant/events.ts`). It is signed with HMAC-SHA256
+over `<timestamp>.<body>` under `ASSISTANT_EVENT_SECRET`, the same secret the
+assistant holds; a test vector pinned in both repositories keeps the two
+signatures one. Best effort: one attempt, a failure logged. Without
+`ASSISTANT_URL` or `ASSISTANT_EVENT_SECRET` nothing is sent.
+
 | Tool                 | Writes | What it does                                        |
 | -------------------- | ------ | --------------------------------------------------- |
 | `get_business_info`  | —      | Contact details, notes, today's date in the business timezone |
```

```bash
git add -A
git commit -m "feat(assistant): a kapora paid on an assistant booking is told to the assistant, signed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 11: The widget keeps looking while the owner holds the conversation

Rezerve's half of Task 1. A pure `nextPoll` decides each next poll:
- **While an answer is coming:** every second, the visitor's next message held back, until a minute after their message.
- **While the owner holds the conversation:** every ten seconds, the visitor free to write, until half an hour after the last news.
- **Otherwise:** no poll.

The widget asks the owner's question only while the chat is open and the tab in view. Opening the chat again, or coming back to the tab, polls at once, and the windows restart. Coming back covers a kapora paid in another tab. Reopening closes 4a's deferred minor: polling did not restart on reopen.

An assistant from before Task 1 sends no `with_owner`, and the widget reads that as no owner.

**Files:**
- Modify: `lib/assistant/chat-client.ts`, `components/assistant/chat-widget.tsx`, `README.md`
- Test: `lib/assistant/chat-client.test.ts`, `e2e/assistant-widget.spec.ts`

**Interfaces:**
- Consumes: the page's `with_owner` (Task 1).
- Produces, in `lib/assistant/chat-client.ts`:
  - `ChatPage.with_owner?: boolean`;
  - `ANSWER_WAIT_MS = 60_000` and `OWNER_WAIT_MS = 1_800_000`;
  - `nextPoll(page: Pick<ChatPage, "pending" | "with_owner">, now: number, until: { answer: number; owner: number }): { afterMs: number; waiting: boolean } | null`.

- [ ] **Step 1: Write the failing tests**

Modify `lib/assistant/chat-client.test.ts`:

```diff
--- a/lib/assistant/chat-client.test.ts
+++ b/lib/assistant/chat-client.test.ts
@@ -3,6 +3,7 @@ import {
   type ChatMessage,
   fetchMessages,
   mergeMessages,
+  nextPoll,
   unconfirmedDrafts,
   VISITOR_KEY,
   visitorId,
@@ -109,3 +110,51 @@ describe("fetchMessages", () => {
     expect(new Headers(init.headers).get("X-Visitor-Id")).toBe(ID);
   });
 });
+
+describe("nextPoll", () => {
+  const NOW = 1_000_000;
+  const until = { answer: NOW + 30_000, owner: NOW + 600_000 };
+
+  it("asks every second while an answer is coming, holding the next message", () => {
+    expect(nextPoll({ pending: true, with_owner: false }, NOW, until)).toEqual({
+      afterMs: 1_000,
+      waiting: true,
+    });
+  });
+
+  it("gives up on an answer a minute after the message", () => {
+    expect(
+      nextPoll({ pending: true, with_owner: false }, until.answer, until)
+    ).toBeNull();
+  });
+
+  it("looks every ten seconds while the owner holds the conversation, the visitor free to write", () => {
+    // The owner answers when they can (spec §7.6), long after a minute.
+    expect(nextPoll({ pending: false, with_owner: true }, NOW, until)).toEqual({
+      afterMs: 10_000,
+      waiting: false,
+    });
+  });
+
+  it("still looks for the owner once a message's minute is up", () => {
+    expect(
+      nextPoll({ pending: true, with_owner: true }, until.answer, until)
+    ).toEqual({ afterMs: 10_000, waiting: false });
+  });
+
+  it("stops looking for the owner half an hour after the last news", () => {
+    expect(
+      nextPoll({ pending: false, with_owner: true }, until.owner, until)
+    ).toBeNull();
+  });
+
+  it("stops once nothing is coming", () => {
+    expect(
+      nextPoll({ pending: false, with_owner: false }, NOW, until)
+    ).toBeNull();
+  });
+
+  it("reads an assistant from before with_owner as no owner", () => {
+    expect(nextPoll({ pending: false }, NOW, until)).toBeNull();
+  });
+});
```

The e2e tests stand in for an assistant whose conversation the owner holds. The visitor's own message shows at once as a draft, so the first test waits for the first poll before it counts polls.

Modify `e2e/assistant-widget.spec.ts`:

```diff
--- a/e2e/assistant-widget.spec.ts
+++ b/e2e/assistant-widget.spec.ts
@@ -77,6 +77,55 @@ async function stubAssistant(page: Page, { failFirstPost = false } = {}) {
   return { attempts, posted, polled };
 }
 
+/**
+ * A stand-in assistant whose conversation the business owner holds (spec
+ * §7.6): each message is stored and passed on, and the owner's reply comes
+ * whenever the test sends it.
+ */
+async function stubOwnerHolding(page: Page) {
+  const conversation: Record<string, unknown>[] = [];
+  const polled: number[] = [];
+  const cors = {
+    "access-control-allow-origin": "*",
+    "access-control-allow-methods": "GET, POST",
+    "access-control-allow-headers": "content-type, x-visitor-id",
+  };
+  const add = (direction: "in" | "out", text: string, id: string | null) =>
+    conversation.push({
+      seq: conversation.length + 1,
+      direction,
+      text,
+      actions: [],
+      client_message_id: id,
+    });
+  await page.route(`${ASSISTANT}/web/demo/messages**`, (route: Route) => {
+    const request = route.request();
+    if (request.method() === "OPTIONS") {
+      return route.fulfill({ status: 204, headers: cors });
+    }
+    if (request.method() === "POST") {
+      const body = request.postDataJSON() as Posted;
+      add("in", body.text, body.client_message_id);
+      return route.fulfill({
+        status: 202,
+        headers: cors,
+        json: { status: "accepted" },
+      });
+    }
+    polled.push(Date.now());
+    const after = Number(new URL(request.url()).searchParams.get("after"));
+    return route.fulfill({
+      headers: cors,
+      json: {
+        messages: conversation.filter((m) => Number(m.seq) > after),
+        pending: false,
+        with_owner: true,
+      },
+    });
+  });
+  return { polled, reply: (text: string) => add("out", text, null) };
+}
+
 test("a visitor asks the assistant and gets a kapora button", async ({
   page,
 }) => {
@@ -170,3 +219,53 @@ test("a message that failed to send is retried as itself, and nothing typed mean
   expect(assistant.attempts[1]).toEqual(assistant.attempts[0]);
   await expect(chat.getByLabel("Mesajınız")).toHaveValue("bir de manikür");
 });
+
+test("while the owner holds the conversation, the widget looks every ten seconds and the visitor can write", async ({
+  page,
+}) => {
+  const owner = await stubOwnerHolding(page);
+  await page.goto("/r/demo");
+  await page.getByRole("button", { name: "Asistana yazın" }).click();
+  const chat = page.getByRole("region", { name: "Asistan" });
+  await chat.getByLabel("Mesajınız").fill("fiyatta indirim olur mu?");
+  await chat.getByRole("button", { name: "Gönder" }).click();
+  // The first look finds the message passed on to the owner.
+  await expect.poll(() => owner.polled.length).toBe(1);
+
+  // The owner answers when they can: nothing is "typing", and the visitor
+  // can add to what they asked meanwhile.
+  await expect(chat.getByRole("status")).toBeHidden();
+  await chat.getByLabel("Mesajınız").fill("bir de 16:00 olur mu?");
+  await expect(chat.getByRole("button", { name: "Gönder" })).toBeEnabled();
+
+  // Slowly: not every second, but within ten.
+  const polls = owner.polled.length;
+  owner.reply("İşletme: 16:00 olur, indirim yapamıyoruz.");
+  await page.waitForTimeout(3_000);
+  expect(owner.polled).toHaveLength(polls);
+  await expect(
+    chat.getByRole("log").getByText("İşletme: 16:00 olur")
+  ).toBeVisible({ timeout: 12_000 });
+});
+
+test("what came while the chat was closed is there when it opens again", async ({
+  page,
+}) => {
+  const owner = await stubOwnerHolding(page);
+  await page.goto("/r/demo");
+  await page.getByRole("button", { name: "Asistana yazın" }).click();
+  const chat = page.getByRole("region", { name: "Asistan" });
+  await chat.getByLabel("Mesajınız").fill("merhaba");
+  await chat.getByRole("button", { name: "Gönder" }).click();
+  await expect(chat.getByRole("log").getByText("merhaba")).toHaveCount(1);
+
+  await chat.getByRole("button", { name: "Kapat" }).click();
+  owner.reply("İşletme: Merhaba, buyurun.");
+  await page.getByRole("button", { name: "Asistana yazın" }).click();
+  // At once: not at the next ten-second look.
+  await expect(
+    page
+      .getByRole("region", { name: "Asistan" })
+      .getByText("İşletme: Merhaba, buyurun.")
+  ).toBeVisible({ timeout: 3_000 });
+});
```

- [ ] **Step 2: Run them to see them fail, the e2e against the widget as it is**

```bash
env $REZERVE_TEST_ENV bunx vitest run lib/assistant/chat-client.test.ts
env $REZERVE_E2E_ENV bun run db:migrate && env $REZERVE_E2E_ENV bun run seed:demo
env $REZERVE_E2E_ENV bun run build && env $REZERVE_E2E_ENV bunx playwright test e2e/assistant-widget.spec.ts
```

Expected:
- **Vitest:** 7 failed, `nextPoll is not a function`.
- **Playwright,** 2 failed and 4 passed:
  - the owner test, whose poll count does not hold still;
  - the reopen test, whose reply is not there within 3 s.

- [ ] **Step 3: Implement**

Modify `lib/assistant/chat-client.ts`:

```diff
--- a/lib/assistant/chat-client.ts
+++ b/lib/assistant/chat-client.ts
@@ -15,7 +15,37 @@ export type ChatMessage = {
   client_message_id: string | null;
 };
 
-export type ChatPage = { messages: ChatMessage[]; pending: boolean };
+export type ChatPage = {
+  messages: ChatMessage[];
+  /** A message of the visitor's is still to be answered. */
+  pending: boolean;
+  /** The business owner holds the conversation (spec §7.6). Absent from an
+   * assistant older than the owner relay. */
+  with_owner?: boolean;
+};
+
+/** How long to look for an answer after a message, and for the owner's. */
+export const ANSWER_WAIT_MS = 60_000;
+export const OWNER_WAIT_MS = 30 * 60_000;
+
+/**
+ * After a poll, when to poll again: every second while an answer is coming,
+ * holding the visitor's next message back, until a minute after theirs;
+ * every ten seconds while the owner holds the conversation, the visitor free
+ * to write, until half an hour after the last news; otherwise not at all.
+ */
+export function nextPoll(
+  page: Pick<ChatPage, "pending" | "with_owner">,
+  now: number,
+  until: { answer: number; owner: number }
+): { afterMs: number; waiting: boolean } | null {
+  if (page.pending && now < until.answer)
+    return { afterMs: 1_000, waiting: true };
+  if (page.with_owner && now < until.owner) {
+    return { afterMs: 10_000, waiting: false };
+  }
+  return null;
+}
 
 /**
  * A message the widget shows before the service has stored it. `failed` when
```

Modify `components/assistant/chat-widget.tsx`:

```diff
--- a/components/assistant/chat-widget.tsx
+++ b/components/assistant/chat-widget.tsx
@@ -12,25 +12,26 @@ import {
 import { Button, buttonVariants } from "@/components/ui/button";
 import { Input } from "@/components/ui/input";
 import {
+  ANSWER_WAIT_MS,
   type ChatAction,
   type ChatMessage,
+  type ChatPage,
   type Draft,
   fetchMessages,
   mergeMessages,
+  nextPoll,
+  OWNER_WAIT_MS,
   sendMessage,
   unconfirmedDrafts,
   visitorId,
 } from "@/lib/assistant/chat-client";
 import { cn } from "@/lib/utils";
 
-/** How often to ask for the answer, and for how long before giving up. */
-const POLL_EVERY_MS = 1_000;
-const POLL_FOR_MS = 60_000;
-
 /**
  * The chat assistant on a business's booking page (spec §6.4). Closed, it is
  * one button and has sent nothing anywhere; opened, it is a conversation with
- * rezerve-assistant, polled while an answer is on its way.
+ * rezerve-assistant, polled while an answer is on its way, and slowly while
+ * the business owner holds the conversation (nextPoll).
  */
 export function ChatWidget({
   slug,
@@ -50,47 +51,77 @@ export function ChatWidget({
   const [text, setText] = useState("");
   const visitor = useRef<string | null>(null);
   const lastSeq = useRef(0);
-  const pollUntil = useRef(0);
+  // What the last poll said, and until when each kind of news is looked for.
+  const last = useRef<Pick<ChatPage, "pending" | "with_owner">>({
+    pending: false,
+  });
+  const answerUntil = useRef(0);
+  const ownerUntil = useRef(0);
+  // Open: the owner's reply is looked for only while someone can see it.
+  const shown = useRef(false);
   const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
   const launcher = useRef<HTMLButtonElement>(null);
   const input = useRef<HTMLInputElement>(null);
   const logEnd = useRef<HTMLLIElement>(null);
 
-  /** One poll: fold in what is new. True while an answer is still coming. */
-  const refresh = useCallback(async (): Promise<boolean> => {
+  /** One poll: fold in what is new, and keep what it says is coming. */
+  const refresh = useCallback(async () => {
     const id = visitor.current;
-    if (!id) return false;
+    if (!id) return;
     const page = await fetchMessages(assistantUrl, slug, id, lastSeq.current);
     if (page.messages.length > 0) {
       lastSeq.current = page.messages[page.messages.length - 1].seq;
       setMessages((shown) => mergeMessages(shown, page.messages));
       setDrafts((sent) => unconfirmedDrafts(sent, page.messages));
+      ownerUntil.current = Date.now() + OWNER_WAIT_MS;
     }
-    return page.pending;
+    last.current = page;
   }, [assistantUrl, slug]);
 
+  /** Poll now, then as long as nextPoll says. */
   const poll = useCallback(() => {
     clearTimeout(timer.current);
-    pollUntil.current = Date.now() + POLL_FOR_MS;
-    setWaiting(true);
     const tick = async () => {
-      let pending = true;
       try {
-        pending = await refresh();
+        await refresh();
       } catch {
-        // A dropped poll: the next one asks again.
+        // A dropped poll: decide on what the last one said.
       }
-      if (pending && Date.now() < pollUntil.current) {
-        timer.current = setTimeout(tick, POLL_EVERY_MS);
-      } else {
-        setWaiting(false);
+      const step = nextPoll(last.current, Date.now(), {
+        answer: answerUntil.current,
+        owner: ownerUntil.current,
+      });
+      setWaiting(step?.waiting ?? false);
+      const seen = shown.current && document.visibilityState === "visible";
+      if (step && (step.waiting || seen)) {
+        timer.current = setTimeout(tick, step.afterMs);
       }
     };
     void tick();
   }, [refresh]);
 
+  /** Look from now: for an answer for a minute, the owner's for half an hour. */
+  const lookAgain = useCallback(() => {
+    const now = Date.now();
+    answerUntil.current = now + ANSWER_WAIT_MS;
+    ownerUntil.current = now + OWNER_WAIT_MS;
+    poll();
+  }, [poll]);
+
   useEffect(() => () => clearTimeout(timer.current), []);
 
+  // Back on the page, say from paying the kapora in another tab: what came
+  // meanwhile, and the owner looked for again.
+  useEffect(() => {
+    const returned = () => {
+      if (document.visibilityState === "visible" && shown.current) {
+        if (visitor.current) lookAgain();
+      }
+    };
+    document.addEventListener("visibilitychange", returned);
+    return () => document.removeEventListener("visibilitychange", returned);
+  }, [lookAgain]);
+
   useEffect(() => {
     if (open) input.current?.focus();
   }, [open]);
@@ -103,21 +134,26 @@ export function ChatWidget({
 
   function openChat() {
     setOpen(true);
-    if (visitor.current) return;
-    let storage: Storage | null = null;
-    try {
-      storage = window.localStorage;
-    } catch {
-      // Storage refused: the conversation lasts as long as the page.
+    shown.current = true;
+    if (!visitor.current) {
+      let storage: Storage | null = null;
+      try {
+        storage = window.localStorage;
+      } catch {
+        // Storage refused: the conversation lasts as long as the page.
+      }
+      const { id, returning } = visitorId(storage);
+      visitor.current = id;
+      // A new visitor has no conversation to fetch yet.
+      if (!returning) return;
     }
-    const { id, returning } = visitorId(storage);
-    visitor.current = id;
-    // A returning visitor's conversation is kept by the service: show it.
-    if (returning) poll();
+    // The conversation the service keeps, with whatever came while closed.
+    lookAgain();
   }
 
   function closeChat() {
     setOpen(false);
+    shown.current = false;
     requestAnimationFrame(() => launcher.current?.focus());
   }
 
@@ -149,7 +185,10 @@ export function ChatWidget({
       markFailed(draft.clientMessageId, true);
       return;
     }
-    poll();
+    // Its answer is coming: the next message waits for it.
+    last.current = { ...last.current, pending: true };
+    setWaiting(true);
+    lookAgain();
   }
 
   async function send(event: FormEvent<HTMLFormElement>) {
```

```bash
bunx biome check --write lib/assistant/chat-client.ts lib/assistant/chat-client.test.ts \
  components/assistant/chat-widget.tsx e2e/assistant-widget.spec.ts
```

- [ ] **Step 4: Run every check, the e2e suite, and the widget's file three times**

```bash
bun run typecheck && bun run check && env $REZERVE_TEST_ENV bun run test
env $REZERVE_E2E_ENV bun run seed:demo && env $REZERVE_E2E_ENV bun run build
env $REZERVE_E2E_ENV bun run e2e
for i in 1 2 3; do env $REZERVE_E2E_ENV bunx playwright test e2e/assistant-widget.spec.ts; done
```

Expected: 441 passed; the e2e suite 20 passed; the widget's 6 passed three times. The owner test takes about 12 s of real time: it shows the ten-second look.

- [ ] **Step 5: The README, and commit**

Modify `README.md`:

```diff
--- a/README.md
+++ b/README.md
@@ -410,7 +410,11 @@ assistant on `/r/[slug]` for a business with `assistant_enabled`, when
 build can point anywhere. Closed, the widget has sent nothing. Its visitor id,
 a random uuid kept in localStorage, is the conversation's only credential. It
 posts a message, then polls every second while an answer is coming, for at
-most a minute, and a kapora link arrives as a button.
+most a minute, and a kapora link arrives as a button. While the business owner
+holds the conversation, it polls every ten seconds instead, for half an hour
+after the last news, only while the chat is open and the tab in view, and the
+visitor can keep writing. Opening the chat again, or coming back to the tab,
+fetches what came meanwhile.
 
 **The kapora event.** When a kapora is paid for a booking the assistant made,
 Rezerve tells the assistant, which tells the customer in their chat:
```

```bash
git add -A
git commit -m "feat(assistant): the widget keeps looking, slowly, while the owner holds the conversation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Finishing both branches

- One fresh reviewer on the most capable model reads both branches whole: the agent's `6386f94..feat/go-live` and Rezerve's `6d0f675..feat/assistant-go-live`.
- One fix pass for every Critical and Important finding, each red then green, each its own commit. Minors are deferred and listed.
- Then superpowers:finishing-a-development-branch, per repository. A merge to `main` and a push are yours (Global Constraints). The agent's push deploys nothing until Go-live sets `GCP_PROJECT`. Rezerve's push deploys to Vercel, where the widget stays hidden and no event is sent until Go-live sets `ASSISTANT_URL` and `ASSISTANT_EVENT_SECRET`.

---

## Go-live, with you

Each command below is yours to run: it touches production. I read the output with you, and nothing goes on until a step's "Expected" holds. Both branches must be merged and pushed first.

Write down each value as you make it. Each goes into `deploy/config.json` and nowhere else, except where a step says Vercel.

### G1. The accounts and secrets

- [ ] **Google Cloud.** You need a billing account with your card on it. Then:

  ```bash
  brew install --cask gcloud-cli
  gcloud auth login
  gcloud components install beta
  gcloud billing accounts list   # its ACCOUNT_ID is BILLING_ACCOUNT
  ```

  Pick a project id: globally unique, 6–30 characters, such as `rezerve-assistant-<4 random letters>`.
- [ ] **Neon,** for the agent's own database.
  - A new project, `rezerve-assistant`, on AWS Frankfurt (eu-central-1), Postgres 17.
  - Set its compute to 0.25 CU at both ends, minimum and maximum. Neon Free has 100 CU-hours a month, and the compute suspends after 5 idle minutes.
  - The connection: Connect, with **Connection pooling off**, then copy the string.
  - Rewrite the copy: `postgresql://` becomes `postgresql+asyncpg://`, and everything after `?` becomes `ssl=require`. That is `DATABASE_URL`.
- [ ] **Langfuse Cloud EU** (cloud.langfuse.com).
  - Create an organization and a project, then an API key pair under Settings.
  - `OTEL_HEADERS` is `Authorization=Basic <base64>,x-langfuse-ingestion-version=4`, where `<base64>` is `printf '%s' 'pk-lf-…:sk-lf-…' | base64`.
  - `OTEL_TRACES_ENDPOINT` stays as the example has it.
- [ ] **Telegram.**
  - Message @BotFather: `/newbot`, a name such as "Rezerve Asistan", and a username ending in `bot`. Its token is `TELEGRAM_BOT_TOKEN`; note the username too.
  - `TELEGRAM_WEBHOOK_SECRET` is `openssl rand -hex 32`.
- [ ] **The models.**
  - `GEMINI_API_KEY`: from the assistant's own AI Studio project. Free-tier quotas are per project, and evals run on the same key and spend the same quota.
  - `GROQ_API_KEY`.
- [ ] **Three secrets of your own,** each `openssl rand -hex 32`:
  - `ASSISTANT_MCP_TOKEN`, which Vercel gets too;
  - `CUSTOMER_REF_SECRET`, never to change afterwards: every customer's ref would change with it;
  - `ASSISTANT_EVENT_SECRET`, which Vercel gets too.

### G2. The project

- [ ] From `~/repos/deploys/rezerve-assistant`, a dry run first:

  ```bash
  DRY_RUN=1 PROJECT=<project id> BILLING_ACCOUNT=<account id> deploy/setup-gcp.sh
  PROJECT=<project id> BILLING_ACCOUNT=<account id> deploy/setup-gcp.sh
  ```

  Expected: the dry run lists every change. The real run makes them, and ends with the GitHub variables and three config values.
  - **If the budget step fails** (a quota project, or a permission), the rest has run. Make the budget by hand: Billing, Budgets & alerts, Create: the project only, amount 1, alerts at 50% and 100%.
  - **If anything else fails,** fix it and run the script again: it picks up where it stopped.

### G3. GitHub and the config

- [ ] In the repository's Settings, Secrets and variables, Actions, **Variables** (not secrets), add `GCP_PROJECT` and `GCP_WIF_PROVIDER` as printed.
- [ ] Fill in the config, then check and upload it:

  ```bash
  cp deploy/config.example.json deploy/config.json   # git-ignored
  # every REPLACE_ME, from G1 and from setup-gcp.sh's output
  uv run assistant config check deploy/config.json
  PROJECT=<project id> deploy/push-config.sh
  ```

  Expected: `No problems.`, then `The config is version 1.` Before the first deploy no service exists, so nothing more happens.

### G4. The first deploy

- [ ] Actions, CI, Run workflow, on `main`.

  Expected: the test, image and workflow jobs pass, and then `deploy`:
  - the image is pushed;
  - `assistant-migrate` runs migrations 0001–0008 on Neon;
  - the service deploys;
  - `/health` answers.
- [ ] Ask it yourself, at the address the config names:

  ```bash
  curl -fsS https://assistant-<project number>.europe-west1.run.app/health
  ```

  Expected: `{"status":"ok"}`.

### G5. Telegram, Rezerve and the owner

- [ ] Point the bot at the service:

  ```bash
  uv run assistant telegram webhook deploy/config.json
  ```

  Expected: the webhook URL, `Pending updates: 0` and `Last error: none`.
- [ ] **Vercel,** the Rezerve project, Settings, Environment Variables, Production:
  - `ASSISTANT_URL` = `https://assistant-<project number>.europe-west1.run.app`;
  - `ASSISTANT_MCP_TOKEN` and `ASSISTANT_EVENT_SECRET`, the same as in the config.

  Then redeploy the latest production deployment, which picks the variables up. Expected:
  - https://rezerve-iota.vercel.app/r/demo shows "Asistana yazın";
  - a POST to `/api/mcp` no longer answers 503.
- [ ] **The owner's chat.** Send `/id` to the bot, then:

  ```bash
  env -u DATABASE_URL CONFIG_FILE=deploy/config.json uv run assistant owners link demo <chat id>
  ```

  Expected: `demo: handoffs go to Telegram chat <chat id>`.

  `env -u` keeps a `DATABASE_URL` in your shell from winning over the file.

### G6. The live checks

Read the service's logs with:

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="assistant"' \
  --project=<project id> --limit=50 --format='value(timestamp,severity,jsonPayload.message)'
```

- [ ] **1. A widget turn.** On `/r/demo`, write "merhaba".

  Expected: an answer within seconds. That one answer proves several things at once:
  - Cloud Tasks called the service back;
  - its OIDC token reached the app intact, through a public service;
  - Neon and the models work.

  **If no answer comes,** look in the logs for `/internal/turn` and its status. A 401 or 403 means the token did not arrive as Google signed it. **Stop there:** that is a design question, not a setting.
- [ ] **2. `TRUSTED_PROXY_HOPS`.** Read the line the first post logged:

  ```bash
  gcloud logging read 'jsonPayload.logger="assistant.ratelimit"' --project=<project id> \
    --limit=5 --format='value(jsonPayload.message)'
  ```

  A browser sends no `X-Forwarded-For` of its own, so the count is what Google's front end appended.
  - **1:** the default stands.
  - **Any other number:** put `"TRUSTED_PROXY_HOPS": <it>` in `deploy/config.json`, and run `push-config.sh` again.
- [ ] **3. A Telegram booking.** Open `t.me/<bot username>?start=demo`, and book a service without kapora, at least two days ahead.

  Expected: the bot asks, you answer "evet", and the booking shows in Rezerve's panel (demo@rezerve.app) as the assistant's.
- [ ] **4. Its reminder.**

  ```bash
  gcloud tasks list --queue=reminders --location=europe-west1 --project=<project id>
  ```

  Expected: one task, scheduled 24 hours before the booking.
- [ ] **5. The kapora event.** In the same chat, book a service with a kapora, and pay with iyzico's sandbox card: 5528790000000008, 12/30, 123. If the payment page is not iyzico's sandbox, stop: the demo must take no real money.

  Expected: the chat gets "Kaporanız alındı, randevunuz onaylandı ✅ …" within seconds of the confirmation page. That proves Task 10 and the shared signature.
- [ ] **6. The owner relay.** In the widget, ask "indirim yapar mısınız?", which hands off to the owner.

  Expected:
  - your owner chat gets the notice;
  - you answer by replying to it, and the widget shows "İşletme: …" within ten seconds;
  - the visitor can write meanwhile;
  - `/bitir` hands the conversation back.
- [ ] **7. Traces.** In Langfuse, within a minute or two of the turns:
  - each turn is a trace, and one conversation's turns share a session;
  - no request span has a `fastapi.arguments` attribute;
  - no customer text appears;
  - no `/web/` request is traced.
- [ ] **8. A refused call back, retried.** A turn for a conversation that does not exist answers 429, so Cloud Tasks retries it:

  ```bash
  URL=https://assistant-<project number>.europe-west1.run.app
  gcloud tasks create-http-task --queue=turns --location=europe-west1 --project=<project id> \
    --url="$URL/internal/turn/999999999" --method=POST \
    --oidc-service-account-email=assistant-tasks@<project id>.iam.gserviceaccount.com \
    --oidc-token-audience="$URL"
  gcloud tasks list --queue=turns --location=europe-west1 --project=<project id> \
    --format='table(name.basename(),dispatchCount,responseCount,lastAttempt.responseStatus.code)'
  ```

  Expected: its dispatch count grows over a minute, with backoff, its last response 429. Then delete it:

  ```bash
  gcloud tasks delete <its name> --queue=turns --location=europe-west1 --project=<project id>
  ```
- [ ] **9. Logs.**
  - Each line in the command above has a severity.
  - A secret is never in them: searching the logs for `api.telegram.org/bot` finds nothing.
- [ ] **10. The config's versions.**

  ```bash
  gcloud secrets versions list assistant-config --project=<project id>
  ```

  Expected: at most two enabled.
- [ ] **11. $0.** The next day, Billing, Reports, filtered to the project. Expected: 0 after the free tier.

### Switching it off, or back

- **The previous revision:**

  ```bash
  gcloud run revisions list --service=assistant --region=europe-west1 --project=<project id>
  gcloud run services update-traffic assistant --to-revisions=<revision>=100 \
    --region=europe-west1 --project=<project id>
  ```

  Its config version is the one kept for it. The rollback holds until the next push to `main` or `push-config.sh`: each sends all traffic to the newest revision again. To roll forward with neither:

  ```bash
  gcloud run services update-traffic assistant --to-latest \
    --region=europe-west1 --project=<project id>
  ```
- **Dark:**
  - remove `ASSISTANT_URL` in Vercel and redeploy, and the widget is gone;
  - set the bot's webhook to nothing in @BotFather, or call `setWebhook` with an empty `url`, and Telegram stops.

  The budget caps from 4a hold in the meantime.

## After go-live

- The memory notes: the project id, the service URL, the bot's username, and what each live check showed.
- The portfolio (`~/repos/deploys/olbios`): add Rezerve Assistant, with screenshots made the usual way. Then ask which course-derived entry to retire: MoonFlix, MernEats or Zync.
- Not in this plan:
  - a "Telegram'dan yazın" link on Rezerve's booking page; the spec defines the deep link, not a link to it;
  - 4a's remaining deferred minors, listed below.

## 4a's deferred minors, after this plan

**Closed here:**
- `TRUSTED_PROXY_HOPS=0` (Task 4);
- the widget's polls spending Langfuse units (Task 2);
- no startup checks on the config (Task 5).

**Still open:**
- Google's certificates fetched on every call;
- `LeaseBusy` raised inside the turn span;
- `/bitir` not acknowledged to the owner;
- the webhook's own sends raising on a 400/403;
- `telegram_chats` never pruned (KVKK);
- an unknown `/start` slug;
- no sender limit while the owner holds the conversation;
- an owner reply duplicated on a redelivery;
- `owners link` not testing the chat.
