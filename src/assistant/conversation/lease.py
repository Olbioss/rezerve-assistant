"""A conversation's lease: one handler at a time, and never a stale one.

The lease is a row, not an advisory lock, so it works through Neon's
transaction-mode pooler and lapses on its own if an instance dies. While a
handler works, a background task renews it. If a renewal fails, the lease
has lapsed and another handler may hold it, so `check()` stops this one
before it writes anything.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta
from types import TracebackType
from typing import Self

from assistant.conversation.store import Store

log = logging.getLogger(__name__)


class LeaseBusy(Exception):
    """Another handler holds this conversation; the queue tries again later."""


class LeaseLost(Exception):
    """The lease lapsed while this handler worked; it must not write."""


class Lease:
    def __init__(
        self, store: Store, conversation_id: int, *, ttl: timedelta, renew_every: timedelta
    ) -> None:
        self._store = store
        self._conversation_id = conversation_id
        self._owner = uuid.uuid4().hex
        self._ttl = ttl
        self._renew_every = renew_every
        self._lost = False
        self._renewer: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        if not await self._store.claim_lease(self._conversation_id, self._owner, self._ttl):
            raise LeaseBusy(self._conversation_id)
        self._renewer = asyncio.create_task(self._renew())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._renewer is not None:
            self._renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewer
        await self._store.release_lease(self._conversation_id, self._owner)

    def check(self) -> None:
        if self._lost:
            raise LeaseLost(self._conversation_id)

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(self._renew_every.total_seconds())
            try:
                renewed = await self._store.renew_lease(
                    self._conversation_id, self._owner, self._ttl
                )
            except Exception:
                # A renewal that cannot say it succeeded did not: stop the
                # handler rather than let it run on a lease that may lapse.
                log.exception("Renewing the lease on conversation %s failed", self._conversation_id)
                renewed = False
            if not renewed:
                self._lost = True
                return
