"""Settings, read from the environment (and from `.env` in development).

Secrets have no defaults, so a missing one stops the service at startup
rather than at its first customer.
"""

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Just the database, for Alembic: migrating needs no API keys."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/assistant"


class Settings(DatabaseSettings):
    # The widget's origins, comma-separated: "http://localhost:3000,https://rezerve.app".
    allowed_web_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    rezerve_mcp_url: str = "http://localhost:3000/api/mcp"
    assistant_mcp_token: SecretStr
    customer_ref_secret: SecretStr

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    # "<provider>:<model>"; the eval report decides the defaults.
    model_primary: str = "google:gemini-3.5-flash-lite"
    model_fallbacks: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["groq:openai/gpt-oss-120b"]
    )

    run_request_limit: int = 8
    run_token_limit: int = 40_000
    approval_ttl_seconds: int = 600
    lease_ttl_seconds: int = 120
    lease_renew_seconds: int = 30

    @field_validator("allowed_web_origins", "model_fallbacks", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
