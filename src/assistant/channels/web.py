"""The chat widget on Rezerve's booking page (spec §6.4).

The widget keeps a random UUID in localStorage as its visitor id. That
unguessable id is the credential — the same model as a booking's id on its
page — so these endpoints take no other. The widget posts a message, then
polls for the answer while `pending` is true.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, StringConstraints

from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
from assistant.identity import customer_ref
from assistant.services import AppServices, app_services

# Rezerve's own slug rule (lib/slug.ts).
type Slug = Annotated[str, Path(min_length=3, max_length=48, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
type Services = Annotated[AppServices, Depends(app_services)]

router = APIRouter(prefix="/web", tags=["web"])


class PostedMessage(BaseModel):
    visitor_id: UUID
    client_message_id: UUID
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class Accepted(BaseModel):
    status: Literal["accepted"] = "accepted"


class LinkActionOut(BaseModel):
    type: Literal["link"]
    label: str
    url: str


class MessageOut(BaseModel):
    seq: int
    direction: Literal["in", "out"]
    text: str
    actions: list[LinkActionOut]
    # The widget's own id for a message it sent, so it can swap its draft for this.
    client_message_id: str | None


class MessagesOut(BaseModel):
    messages: list[MessageOut]
    pending: bool


class WebChannel:
    name: ChannelName = "web"

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        # The widget polls: a stored reply is a delivered one.
        return f"web:{message.seq}"


@router.post("/{slug}/messages", status_code=202)
async def post_message(slug: Slug, body: PostedMessage, services: Services) -> Accepted:
    message = InboundMessage(
        business_slug=slug,
        channel="web",
        channel_user_id=str(body.visitor_id),
        channel_message_id=str(body.client_message_id),
        text=body.text,
    )
    secret = services.settings.customer_ref_secret.get_secret_value()
    recorded = await services.store.record_inbound(
        message, customer_ref(secret, "web", message.channel_user_id), datetime.now(UTC)
    )
    if recorded.seq is not None:
        await services.queue.enqueue_turn(recorded.conversation_id, recorded.seq)
    return Accepted()


@router.get("/{slug}/messages")
async def get_messages(
    slug: Slug,
    visitor_id: UUID,
    services: Services,
    after: Annotated[int, Query(ge=0)] = 0,
) -> MessagesOut:
    page = await services.store.messages_after(slug, "web", str(visitor_id), after)
    return MessagesOut(
        messages=[
            MessageOut(
                seq=message.seq,
                direction=message.direction,
                text=message.text,
                actions=[LinkActionOut.model_validate(action) for action in message.actions],
                client_message_id=message.client_message_id,
            )
            for message in page.messages
        ],
        pending=page.pending,
    )
