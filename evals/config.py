"""What an eval run needs on top of the service's own settings, from the
same `.env`."""

from assistant.config import Settings


class EvalSettings(Settings):
    # The agent's tables for eval conversations, apart from development's.
    eval_database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/assistant_eval"
    # Rezerve's local database: reset and seeded before each scenario, read to grade it.
    rezerve_database_url: str = "postgresql://postgres:postgres@localhost:5433/rezerve_eval"
    # The simulated customer and the judge.
    simulator_model: str = "groq:openai/gpt-oss-120b"
    judge_model: str = "groq:openai/gpt-oss-120b"
    # The evals measure the agent, not its budgets: a whole run and its trials
    # spend more in a day than production's caps allow.
    daily_request_cap: int | None = None
    conversation_daily_token_cap: int | None = None
    sender_message_limit: int | None = None
