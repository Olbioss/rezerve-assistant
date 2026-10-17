# Rezerve Assistant

An AI receptionist for Turkish local businesses. A customer writes in Turkish
in the chat widget on a business's [Rezerve](https://github.com/Olbioss/rezerve)
booking page (Telegram and WhatsApp come later). The assistant finds a free
time and books it, taking a kapora when the service asks for one. It moves
and cancels bookings under the business's policy, answers from the
business's own information, and steps aside when it should not answer.

It books through the MCP server Rezerve exposes, so every booking runs
Rezerve's own booking code and its Postgres exclusion constraint. No write
happens without the customer's "evet" to a confirmation that code rendered
from the exact arguments that will run.

**Status:** phase 3 of 5. The agent core, the web channel and the evals run locally.
The design is in
[docs/superpowers/specs/2026-10-14-rezerve-assistant-design.md](docs/superpowers/specs/2026-10-14-rezerve-assistant-design.md).

## How a message is answered

1. **Stored once.** The widget posts a message and gets `202`; a redelivery
   with the same `client_message_id` is stored once.
2. **One handler per conversation.** A turn is queued and claims the
   conversation's lease (a row, renewed while it works).
3. **One answer per burst.** Everything written since the last answer is
   handled in one Pydantic AI run with Rezerve's tools. Who the customer is
   travels in MCP headers, never in arguments the model writes.
4. **Writes wait for a yes.** A write ends the run. Code shows the
   confirmation, and the customer's next message, written after the
   confirmation appeared, approves it, denies it, or changes the plan.
5. **Every customer gets an answer.** If every model fails, the reply is a
   link to the booking page.

## The race the confirmation gate had

The approval design exists so that nothing is written until the customer has
read exactly what will happen. It had a race anyway, one that survived the
design, the spec and the implementation, and an independent review of the
whole branch found it.

**The race.** A message that arrived while the assistant was still preparing a
confirmation was answered in the next batch, against that confirmation. So a
customer double-texting "tamam" while "Yazıyor…" showed approved a booking
they had never seen. A pre-typed "evet" on a cancellation inside 24 hours gave
the kapora away before the warning reached them.

**The fix.** Each pending approval now records the message that showed it.
Only what the customer writes after that message can answer it, and the
widget sends one message at a time.

**The test.**
`test_a_message_written_before_the_confirmation_cannot_approve_it`
(`tests/integration/test_turn.py`) types "evet" while the confirmation is
being prepared:
- **without the boundary,** that "evet" books the appointment before the
  customer has seen it;
- **with it,** nothing is written until an "evet" that came after.

## Run it locally

You need Docker, [uv](https://docs.astral.sh/uv/), a Rezerve checkout at
`../rezerve`, and a Gemini or Groq API key.

```bash
cp .env.example .env    # set ASSISTANT_MCP_TOKEN, CUSTOMER_REF_SECRET and a model key
docker compose up -d db # Postgres on localhost:5433: assistant, assistant_test, rezerve

# Rezerve, on the same Postgres, with its demo businesses (in ../rezerve):
export DATABASE_URL=postgresql://postgres:postgres@localhost:5433/rezerve
bun run db:migrate && bun run seed:demo
ASSISTANT_MCP_TOKEN=<the same token> ASSISTANT_URL=http://localhost:8000 \
  NEXT_PUBLIC_APP_URL=http://localhost:3000 BETTER_AUTH_URL=http://localhost:3000 \
  EMAIL_DELIVERY=off bun dev

docker compose up assistant   # migrates, then serves on :8000
```

Then open <http://localhost:3000/r/demo> and write to the assistant. To run
the service on the host instead:
`uv run alembic upgrade head && uv run uvicorn assistant.main:create_app --factory --reload`.

## Tests

```bash
docker compose up -d db
uv run pytest            # unit, Postgres integration, and contract tests
uv run ruff check && uv run ruff format --check && uv run pyright
```

The agent tests use scripted models and an in-process fake Rezerve: no
network, no quota.

## Evals

`evals/` holds 32 Turkish conversations, scripted or played by a model with a
goal. Each is graded on the bookings it leaves in Rezerve's database, never on
what the assistant says it did: the right booking under the right name,
nothing else written for anyone, no write without an approved "evet", no
price that isn't real, no other customer's details. A few are also scored for
tone by a judge model.

### Running them

You need the local setup above, and Rezerve's eval business on a database of
its own: it cannot share one with the demo businesses.

```bash
docker compose exec db createdb -U postgres rezerve_eval   # once
cd ../rezerve
export DATABASE_URL=postgresql://postgres:postgres@localhost:5433/rezerve_eval
bun run db:migrate && bun run seed:eval
ASSISTANT_MCP_TOKEN=<the same token> EMAIL_DELIVERY=off \
  NEXT_PUBLIC_APP_URL=http://localhost:3000 BETTER_AUTH_URL=http://localhost:3000 bun dev
```

Then, here:

```bash
uv run python -m evals.run --suite smoke   # six scenarios
uv run python -m evals.run --suite full    # all of them, then the six again for the noise band
```

Each run writes `evals/results/<run>.json` and a Markdown report beside it.
- **Free tiers run out.** A run that meets a daily limit stops, keeps what it
  finished, and carries on with `--resume <run>` after the reset.
- **`--only <id> …`** runs chosen scenarios.
- **`--agent-model <provider:model>`** runs the agent on that one model,
  without a fallback.
- **Don't run `pytest` during an eval run:** one of its tests resets the eval
  business.

### The first baseline

Run `2026-10-17T08-31-41Z-full`, on a Saturday, with Gemini 3.5 Flash-Lite
falling back to Groq's gpt-oss-120b:

| Measure | Result | Target | Met |
|---|---|---|---|
| Task success | 77% (24/31) | ≥ 90% | ✗ |
| Injection scenarios with no unauthorized write | 100% (4/4) | 100% | ✓ |
| Writes without an approval | 0 | 0 | ✓ |
| Date resolution | 73% (11/15) | ≥ 95% | ✗ |

| Tag | Success |
|---|---|
| booking | 58% (7/12) |
| kapora | 60% (3/5) |
| reschedule | 75% (3/4) |
| cancel | 100% (4/4) |
| date | 60% (9/15) |
| faq | 100% (3/3) |
| handoff | 100% (4/4) |
| injection | 75% (3/4) |
| judge | 57% (4/7) |

The whole run cost nothing on the free tiers; at list prices it would have cost
$0.11. Turns took 1.1 s at the median and 3.3 s at p95. Rerunning six
scenarios changed one result in six, so differences smaller than that are
noise.

What failed, by kind:
- **A made-up name.** Two simulated customers were booked as "Müşteri"
  ("customer"): the model never asked their names.
- **Dates.** On a Saturday, "cumartesi" was read as tomorrow, and "haftaya
  çarşamba" as the Wednesday after next.
- **Saturday itself.** "cumartesi", written on a Saturday, may mean today or
  next week. Two scenarios graded only one reading.
- **A wrong expectation.** A customer haggling for a discount was handed to the
  owner, as the spec asks; the scenario expected no hand-off.

## The contract with Rezerve

`contract/rezerve-mcp-tools.json` is the output of `bun run mcp:schema` in
Rezerve: the tools exactly as `/api/mcp` serves them. The fake Rezerve
serves that file verbatim. `tests/contract` fails when it, the fake, and
this service's expectations drift apart.
