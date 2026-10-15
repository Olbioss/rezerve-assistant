"""The customer ref: who a chat customer is, as Rezerve sees them.

Rezerve stores it as an opaque string and checks only its shape,
`^(web|telegram|whatsapp|eval):[a-z2-7]{26}$`. It is an HMAC of the
channel's own user id, so a Telegram id or a phone number never leaves this
service, and nothing but this service can compute a customer's ref.
"""

import base64
import hashlib
import hmac
from typing import Literal

type RefChannel = Literal["web", "telegram", "whatsapp", "eval"]


def customer_ref(secret: str, channel: RefChannel, channel_user_id: str) -> str:
    digest = hmac.new(
        secret.encode(), f"{channel}:{channel_user_id}".encode(), hashlib.sha256
    ).digest()
    return f"{channel}:{base64.b32encode(digest).decode('ascii').lower()[:26]}"
