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
