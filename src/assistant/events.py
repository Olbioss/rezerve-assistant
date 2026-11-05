"""Rezerve's events (spec §5, §6.5). Each is signed with HMAC-SHA256 over its
timestamp and body, and refused outside a five-minute window, so a request
seen once cannot be sent again later.

Best effort on both sides: Rezerve sends each event once, and the
confirmation page and email have already told the customer.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from assistant.services import AppServices, app_services

TIMESTAMP_HEADER = "X-Rezerve-Timestamp"
SIGNATURE_HEADER = "X-Rezerve-Signature"
WINDOW_SECONDS = 300
CONFIRMED = "Kaporanız alındı, randevunuz onaylandı ✅ {service_name} · {local_label}"

type Services = Annotated[AppServices, Depends(app_services)]

router = APIRouter(prefix="/events", include_in_schema=False)


def signature(secret: str, timestamp: str, body: bytes) -> str:
    """What Rezerve puts in X-Rezerve-Signature."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


class Event(BaseModel):
    type: str


class BookingConfirmed(BaseModel):
    """A kapora paid: Rezerve confirmed an assistant booking."""

    type: Literal["booking.confirmed"]
    booking_id: str
    business_slug: str
    customer_ref: str
    service_name: str
    local_label: str


@router.post("/rezerve", status_code=202)
async def rezerve_event(request: Request, services: Services) -> dict[str, str]:
    secret = services.settings.assistant_event_secret
    if secret is None:
        raise HTTPException(status_code=404)
    body = await request.body()
    timestamp = request.headers.get(TIMESTAMP_HEADER, "")
    given = request.headers.get(SIGNATURE_HEADER, "")
    fresh = timestamp.isdigit() and abs(time.time() - int(timestamp)) <= WINDOW_SECONDS
    expected = signature(secret.get_secret_value(), timestamp, body)
    if not (fresh and hmac.compare_digest(given.encode(), expected.encode())):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        if Event.model_validate_json(body).type != "booking.confirmed":
            return {"status": "ignored"}
        event = BookingConfirmed.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="malformed event") from error
    now = datetime.now(UTC)
    if not await services.store.first_seen(f"{event.type}:{event.booking_id}", now):
        return {"status": "duplicate"}
    conversation_id = await services.store.latest_conversation(
        event.business_slug, event.customer_ref
    )
    if conversation_id is None:
        return {"status": "no_conversation"}
    text = CONFIRMED.format(service_name=event.service_name, local_label=event.local_label)
    seq = await services.store.add_reply(conversation_id, text)
    # The turn sends what waits unsent; there is nothing else to answer.
    await services.queue.enqueue_turn(conversation_id, seq)
    return {"status": "accepted"}
