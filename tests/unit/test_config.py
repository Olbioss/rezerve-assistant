import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant.config import DatabaseSettings, Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
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
            "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
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


@pytest.mark.parametrize("hops", [0, -1])
def test_the_client_is_never_an_entry_the_client_wrote(hops: int) -> None:
    # With 0, the address taken would be X-Forwarded-For's leftmost entry,
    # which a client writes itself: each post could name a new one.
    with pytest.raises(ValidationError, match="trusted_proxy_hops"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            assistant_mcp_token="t",
            customer_ref_secret="s",
            trusted_proxy_hops=hops,
        )


def test_cloud_tasks_needs_a_project_and_an_invoker() -> None:
    with pytest.raises(ValidationError, match="GCP_PROJECT and TASKS_INVOKER_SA"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            assistant_mcp_token="t",
            customer_ref_secret="s",
            queue_backend="cloud_tasks",
        )


NEON = "ep-quiet-sun-a1b2c3d4.eu-central-1.aws.neon.tech"


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("postgresql://u:p@localhost:5433/assistant", r"must start with postgresql\+asyncpg://"),
        (
            f"postgresql+asyncpg://u:p@{NEON}/neondb?sslmode=require&channel_binding=require",
            "asyncpg takes ssl=require",
        ),
        (
            "postgresql+asyncpg://u:p@ep-quiet-sun-a1b2c3d4-pooler.eu-central-1.aws.neon.tech/neondb?ssl=require",
            "direct endpoint",
        ),
    ],
)
def test_the_database_url_is_one_asyncpg_opens(url: str, problem: str) -> None:
    # Neon's console gives "postgresql://…?sslmode=require&channel_binding=require".
    # asyncpg takes neither parameter: every connection would fail, the
    # migration job's first.
    with pytest.raises(ValidationError, match=problem):
        DatabaseSettings(_env_file=None, database_url=url)  # pyright: ignore[reportCallIssue]


def test_neons_direct_endpoint_over_tls_is_accepted() -> None:
    url = f"postgresql+asyncpg://u:p@{NEON}/neondb?ssl=require"
    settings = DatabaseSettings(_env_file=None, database_url=url)  # pyright: ignore[reportCallIssue]
    assert settings.database_url == url


def test_a_bot_needs_the_secret_its_webhook_checks() -> None:
    # Without it the webhook refuses every update: no customer or owner on
    # Telegram would get an answer.
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN needs TELEGRAM_WEBHOOK_SECRET"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            assistant_mcp_token="t",
            customer_ref_secret="s",
            telegram_bot_token="123:abc",
        )


@pytest.mark.parametrize("secret", ["has a space", "x" * 257, "şifre", ""])
def test_the_webhook_secret_is_one_telegram_accepts(secret: str) -> None:
    with pytest.raises(ValidationError, match="Telegram accepts 1 to 256"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            assistant_mcp_token="t",
            customer_ref_secret="s",
            telegram_bot_token="123:abc",
            telegram_webhook_secret=secret,
        )


def test_cloud_tasks_calls_back_over_https() -> None:
    # Its OIDC token goes with each call: never over plain HTTP.
    with pytest.raises(ValidationError, match="needs an https PUBLIC_BASE_URL"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            assistant_mcp_token="t",
            customer_ref_secret="s",
            queue_backend="cloud_tasks",
            gcp_project="p",
            tasks_invoker_sa="assistant-tasks@p.iam.gserviceaccount.com",
            public_base_url="http://assistant-1.europe-west1.run.app",
        )
