"""A sliding window per key, kept in this process: the widget's posts per
client address (spec §5).

Each Cloud Run instance keeps its own window, and the service runs at most
two, so one address gets at most twice the limit. The daily model-request cap
stays the hard stop; this keeps one address from spending it alone.
"""

import time
from collections import deque
from collections.abc import Callable

from starlette.requests import Request


class SlidingWindow:
    def __init__(
        self,
        *,
        limit: int,
        seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._seconds = seconds
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def __len__(self) -> int:
        return len(self._hits)

    def allow(self, key: str) -> bool:
        """Count one hit for `key`, unless its window is already full."""
        now = self._clock()
        if key not in self._hits and len(self._hits) >= self._max_keys:
            self._forget_idle(now)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._seconds:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True

    def _forget_idle(self, now: float) -> None:
        for key in [key for key, hits in self._hits.items() if hits[-1] <= now - self._seconds]:
            del self._hits[key]


def client_ip(request: Request, *, hops: int) -> str:
    """The address `hops` places from the right of X-Forwarded-For: one the
    platform in front of the service appended, which a client cannot forge.
    Entries further left came from the client itself."""
    header = request.headers.get("x-forwarded-for", "")
    forwarded = [part.strip() for part in header.split(",") if part.strip()]
    if len(forwarded) >= hops:
        return forwarded[-hops]
    return request.client.host if request.client else "unknown"
