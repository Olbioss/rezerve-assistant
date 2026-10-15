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
