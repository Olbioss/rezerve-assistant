"""Settings, read from the environment (and from `.env` in development).

Secrets have no defaults, so a missing one stops the service at startup
rather than at its first customer. In production every setting comes from one
Secret Manager secret (JSON), mounted as a file named by CONFIG_FILE (spec
§7.11); the environment still wins over it, for a one-off override.
"""

import os
import re
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import parse_qs, urlsplit

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

    @field_validator("database_url")
    @classmethod
    def _asyncpg_opens(cls, url: str) -> str:
        """Neon's console gives "postgresql://…?sslmode=require&channel_binding=require":
        asyncpg takes neither parameter, so every connection would fail. Its
        direct endpoint is the one used: two instances need no pooler."""
        parts = urlsplit(url)
        if parts.scheme != "postgresql+asyncpg":
            raise ValueError("must start with postgresql+asyncpg://")
        if {"sslmode", "channel_binding"} & parse_qs(parts.query).keys():
            raise ValueError("asyncpg takes ssl=require, not sslmode or channel_binding")
        if "-pooler." in (parts.hostname or ""):
            raise ValueError("use Neon's direct endpoint: the host without -pooler")
        return url

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
    sender_message_limit: int | None = 20
    sender_window_seconds: int = 600
    # The widget's posts per client IP, in the same window.
    ip_message_limit: int = 60
    # Which X-Forwarded-For entry is the client, counted from the right: the
    # ones the platform appends. Each instance logs how many a post carried.
    trusted_proxy_hops: int = Field(default=1, ge=1)

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

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _telegram_accepts(cls, secret: SecretStr | None) -> SecretStr | None:
        if secret is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{1,256}", secret.get_secret_value()
        ):
            raise ValueError("Telegram accepts 1 to 256 letters, digits, _ and -")
        return secret

    @model_validator(mode="after")
    def _settings_agree(self) -> Self:
        if self.queue_backend == "cloud_tasks":
            if not (self.gcp_project and self.tasks_invoker_sa):
                raise ValueError("QUEUE_BACKEND=cloud_tasks needs GCP_PROJECT and TASKS_INVOKER_SA")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("QUEUE_BACKEND=cloud_tasks needs an https PUBLIC_BASE_URL")
        if self.telegram_bot_token is not None and self.telegram_webhook_secret is None:
            # The webhook would refuse every update.
            raise ValueError("TELEGRAM_BOT_TOKEN needs TELEGRAM_WEBHOOK_SECRET")
        return self


def from_file(path: Path) -> Settings:
    """Every setting from this JSON file alone: the operator's commands read
    the production config this way, so neither their shell's variables nor a
    .env beside them fills a gap the file has."""

    class FileOnly(Settings):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            return (JsonConfigSettingsSource(settings_cls, json_file=path),)

    return FileOnly()  # pyright: ignore[reportCallIssue]
