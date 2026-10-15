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

**Status:** phase 2 of 5. The agent core and the web channel run locally.
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
   confirmation, and the customer's next message approves it, denies it, or
   changes the plan.
5. **Every customer gets an answer.** If every model fails, the reply is a
   link to the booking page.

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

## The contract with Rezerve

`contract/rezerve-mcp-tools.json` is the output of `bun run mcp:schema` in
Rezerve: the tools exactly as `/api/mcp` serves them. The fake Rezerve
serves that file verbatim. `tests/contract` fails when it, the fake, and
this service's expectations drift apart.
