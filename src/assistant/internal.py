"""Calls from Cloud Tasks (spec §5). Each carries an OIDC token Google signed
for the invoker service account, with this service's URL as its audience;
anything else is refused before a turn can start.

The routes are always mounted. A service on the local queue has no invoker
account, so every call to them is refused there too.
"""

import asyncio
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from assistant.conversation.lease import LeaseBusy
from assistant.services import AppServices, app_services

type Services = Annotated[AppServices, Depends(app_services)]

router = APIRouter(prefix="/internal", include_in_schema=False)

_google = GoogleRequest()


class InvalidToken(Exception):
    """Not a current Google token for this service."""


def _verify(token: str, audience: str) -> Mapping[str, Any]:
    # google-auth leaves this untyped; it returns the token's claims.
    claims: Mapping[str, Any] = id_token.verify_oauth2_token(  # pyright: ignore[reportUnknownMemberType]
        token, _google, audience
    )
    return claims


async def verify_google_token(token: str, audience: str) -> Mapping[str, Any]:
    """Google's own check of signature, issuer, expiry and audience. It fetches
    Google's public keys, so it runs off the event loop."""
    try:
        return await asyncio.to_thread(_verify, token, audience)
    except TransportError:
        # Google's keys out of reach is no fault of the caller's: a 500, retried.
        raise
    except (ValueError, GoogleAuthError) as error:
        raise InvalidToken(str(error)) from error


async def from_cloud_tasks(request: Request, services: Services) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    audience = services.settings.public_base_url.rstrip("/")
    try:
        claims = await services.verify_token(token, audience)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="unauthorized") from None
    invoker = services.settings.tasks_invoker_sa
    if (
        invoker is None
        or claims.get("email") != invoker
        or claims.get("email_verified") is not True
    ):
        raise HTTPException(status_code=403, detail="forbidden")


@router.post("/turn/{conversation_id}", status_code=204, dependencies=[Depends(from_cloud_tasks)])
async def turn(conversation_id: int, services: Services) -> Response:
    try:
        await services.process_turn(conversation_id)
    except LeaseBusy:
        # Another handler has the conversation: Cloud Tasks tries again later.
        raise HTTPException(status_code=429, detail="busy") from None
    return Response(status_code=204)
