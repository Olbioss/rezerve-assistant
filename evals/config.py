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
