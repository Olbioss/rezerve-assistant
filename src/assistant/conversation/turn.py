"""One turn: everything a customer wrote since the last answer, answered once.

`process` claims the conversation's lease, sends whatever a crash left
unsent, then answers its unprocessed messages, oldest first, until none is
left. A burst — "merhaba", then "yarın yer var mı?" — is one batch, one run,
one answer. A batch's outcome is written in one transaction, and its replies
are sent after it and marked sent one by one, so a retry resends only what
never went out (at-least-once, spec §7.2).
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolResults,
    FallbackExceptionGroup,
    ModelAPIError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RunUsage,
    ToolReturnPart,
    UsageLimitExceeded,
    UsageLimits,
)

from assistant.agent.agent import AgentOutput, Handoff, TurnDeps
from assistant.agent.approvals import (
    Refusal,
    ReplyClassifier,
    Resolution,
    classify_by_lexicon,
    propose,
    refusal,
    resolution,
)
from assistant.agent.handoff import booking_page, handoff_reply, unavailable_reply
from assistant.channels.base import Action, Channel, ChannelName
from assistant.config import Settings
from assistant.conversation import history
from assistant.conversation.lease import Lease, LeaseLost
from assistant.conversation.store import (
    ConversationRecord,
    Decision,
    NewApproval,
    OpenApproval,
    Outcome,
    Reply,
    RunRecord,
    Store,
    StoredInbound,
    TurnResult,
)
from assistant.mcp import BusinessInfo, RezerveSession

log = logging.getLogger(__name__)

# Calls code may send back to the model in one turn — a service id it made up,
# an email it forgot to ask for — before the business is handed the customer.
MAX_REFUSALS = 2

type OpenRezerve = Callable[[str, str], RezerveSession]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TurnSettings:
    request_limit: int
    token_limit: int
    approval_ttl: timedelta
    lease_ttl: timedelta
    lease_renew: timedelta
    rezerve_mcp_url: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "TurnSettings":
        return cls(
            request_limit=settings.run_request_limit,
            token_limit=settings.run_token_limit,
            approval_ttl=timedelta(seconds=settings.approval_ttl_seconds),
            lease_ttl=timedelta(seconds=settings.lease_ttl_seconds),
            lease_renew=timedelta(seconds=settings.lease_renew_seconds),
            rezerve_mcp_url=settings.rezerve_mcp_url,
        )


@dataclass
class Meter:
    """What a turn cost, across its agent runs and the reply classifier."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    models_tried: list[str] = field(default_factory=list[str])
    model_used: str | None = None

    def add(self, usage: RunUsage, messages: Sequence[ModelMessage]) -> None:
        self.requests += usage.requests
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.tool_calls += usage.tool_calls
        for message in messages:
            if isinstance(message, ModelResponse):
                for attempt in message.failed_attempts or []:
                    self._saw(attempt.model_name)
                if message.model_name:
                    self._saw(message.model_name)
                    self.model_used = message.model_name

    def add_failure(self, error: BaseException) -> None:
        if isinstance(error, FallbackExceptionGroup):
            for attempt in error.attempts:
                self.requests += 1
                self._saw(attempt.model_name)

    def _saw(self, model_name: str) -> None:
        if model_name not in self.models_tried:
            self.models_tried.append(model_name)


@dataclass
class _Turn:
    """One batch on its way to a TurnResult."""

    processed: list[int]
    started_at: datetime
    meter: Meter = field(default_factory=Meter)
    resolved: tuple[int, Decision] | None = None
    business: BusinessInfo | None = None
    _clock_start: float = field(default_factory=time.monotonic)

    def finish(
        self,
        replies: list[Reply],
        outcome: Outcome,
        *,
        messages: list[ModelMessage] | None = None,
        approval: NewApproval | None = None,
    ) -> TurnResult:
        meter = self.meter
        run = RunRecord(
            started_at=self.started_at,
            duration_ms=int((time.monotonic() - self._clock_start) * 1000),
            model_used=meter.model_used,
            models_tried=list(meter.models_tried),
            requests=meter.requests,
            input_tokens=meter.input_tokens,
            output_tokens=meter.output_tokens,
            tool_calls=meter.tool_calls,
            outcome=outcome,
        )
        return TurnResult(
            processed=self.processed,
            replies=replies,
            history=history.trim(messages) if messages is not None else None,
            run=run,
            resolved=self.resolved,
            approval=approval,
        )


def payment_actions(messages: Sequence[ModelMessage]) -> list[Action]:
    """A payment button for every kapora checkout this turn's writes opened."""
    actions: list[Action] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not (
                isinstance(part, ToolReturnPart)
                and part.tool_name == "create_booking"
                and part.outcome == "success"
            ):
                continue
            content: object = part.content
            if isinstance(content, dict):
                url = cast(dict[str, Any], content).get("checkout_url")
                if isinstance(url, str) and url:
                    actions.append({"type": "link", "label": "Kaporayı öde", "url": url})
    return actions


def _decision(resolution: Resolution) -> Decision:
    if resolution == "approve":
        return "approved"
    return "expired" if resolution == "expired" else "denied"


class TurnProcessor:
    def __init__(
        self,
        *,
        store: Store,
        agent: Agent[TurnDeps, AgentOutput],
        classifier: ReplyClassifier,
        open_rezerve: OpenRezerve,
        channels: Mapping[ChannelName, Channel],
        settings: TurnSettings,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._agent = agent
        self._classifier = classifier
        self._open_rezerve = open_rezerve
        self._channels = channels
        self._settings = settings
        self._clock = clock
        self._limits = UsageLimits(
            request_limit=settings.request_limit, total_tokens_limit=settings.token_limit
        )

    async def process(self, conversation_id: int) -> None:
        """Raises LeaseBusy when another handler holds the conversation."""
        settings = self._settings
        async with Lease(
            self._store,
            conversation_id,
            ttl=settings.lease_ttl,
            renew_every=settings.lease_renew,
        ) as lease:
            conversation = await self._store.conversation(conversation_id)
            await self._send_unsent(conversation)
            while batch := await self._store.unprocessed_inbound(conversation_id):
                result = await self._answer(conversation, batch, lease.check)
                lease.check()
                await self._store.commit_turn(conversation_id, result, self._clock())
                await self._send_unsent(conversation)
                conversation = await self._store.conversation(conversation_id)

    async def _send_unsent(self, conversation: ConversationRecord) -> None:
        channel = self._channels[conversation.channel]
        for message in await self._store.unsent(conversation.id):
            sent_id = await channel.send(conversation.channel_user_id, message)
            await self._store.mark_sent(message.seq, sent_id, self._clock())

    async def _answer(
        self,
        conversation: ConversationRecord,
        batch: list[StoredInbound],
        check_lease: Callable[[], None],
    ) -> TurnResult:
        turn = _Turn(processed=[message.seq for message in batch], started_at=self._clock())
        text = "\n".join(message.text for message in batch)
        try:
            async with self._open_rezerve(
                conversation.business_slug, conversation.customer_ref
            ) as rezerve:
                turn.business = await rezerve.business_info()
                deps = TurnDeps(
                    business=turn.business,
                    services=await rezerve.services(),
                    bookings=await rezerve.my_bookings(),
                )
                return await self._converse(conversation, text, deps, rezerve, turn, check_lease)
        except LeaseLost:
            raise
        except UsageLimitExceeded:
            if turn.business is None:
                raise
            return turn.finish([Reply(handoff_reply(turn.business))], "handoff")
        except (FallbackExceptionGroup, ModelAPIError):
            log.warning("Every model failed for conversation %s", conversation.id, exc_info=True)
            return turn.finish([Reply(self._unavailable(conversation, turn))], "degraded")
        except Exception:
            log.exception("Turn failed for conversation %s", conversation.id)
            return turn.finish([Reply(self._unavailable(conversation, turn))], "error")

    def _unavailable(self, conversation: ConversationRecord, turn: _Turn) -> str:
        page = turn.business.booking_page_url if turn.business else None
        return unavailable_reply(
            page or booking_page(self._settings.rezerve_mcp_url, conversation.business_slug)
        )

    async def _converse(
        self,
        conversation: ConversationRecord,
        text: str,
        deps: TurnDeps,
        rezerve: RezerveSession,
        turn: _Turn,
        check_lease: Callable[[], None],
    ) -> TurnResult:
        approval = await self._store.open_approval(conversation.id)
        if approval is None:
            run = await self._run(
                deps, rezerve, turn, text, history.close_open_calls(conversation.history)
            )
        else:
            decision = await self._decide(approval, text, turn)
            if decision == "approve":
                # The write runs inside the next call: never on a lapsed lease.
                check_lease()
            # Spent from here on, whatever the run below does.
            turn.resolved = (approval.id, _decision(decision))
            run = await self._run(
                deps, rezerve, turn, text, approval.history, resolution(approval, decision)
            )
        new_messages = list(run.new_messages())

        for _ in range(MAX_REFUSALS + 1):
            output = run.output
            if isinstance(output, Handoff):
                reply = Reply(handoff_reply(deps.business))
                return turn.finish([reply], "handoff", messages=run.all_messages())
            if isinstance(output, str):
                if not output.strip():
                    # An empty answer is no answer: send the booking page instead.
                    reply = Reply(self._unavailable(conversation, turn))
                    return turn.finish([reply], "degraded", messages=run.all_messages())
                reply = Reply(output, payment_actions(new_messages))
                return turn.finish([reply], "reply", messages=run.all_messages())
            first, *others = output.approvals
            proposal = await propose(first, deps, rezerve, self._clock())
            if isinstance(proposal, Refusal):
                run = await self._run(
                    deps, rezerve, turn, None, run.all_messages(), refusal(output, proposal.reason)
                )
                new_messages.extend(run.new_messages())
                continue
            return turn.finish(
                [Reply(proposal.text)],
                "confirmation",
                messages=run.all_messages(),
                approval=NewApproval(
                    tool_call_id=first.tool_call_id,
                    tool_name=first.tool_name,
                    args=proposal.args,
                    other_call_ids=[call.tool_call_id for call in others],
                    rendered_text=proposal.text,
                    expires_at=self._clock() + self._settings.approval_ttl,
                ),
            )
        # It kept proposing what could not be proposed: the business takes over.
        reply = Reply(handoff_reply(deps.business))
        return turn.finish(
            [reply], "handoff", messages=history.close_open_calls(run.all_messages())
        )

    async def _decide(self, approval: OpenApproval, text: str, turn: _Turn) -> Resolution:
        if approval.expires_at <= self._clock():
            return "expired"
        decision = classify_by_lexicon(text, approval.tool_name)
        if decision is not None:
            return decision
        result = await self._classifier.classify(approval.rendered_text, text)
        turn.meter.add(result.usage, result.new_messages())
        return result.output

    async def _run(
        self,
        deps: TurnDeps,
        rezerve: RezerveSession,
        turn: _Turn,
        prompt: str | None,
        message_history: Sequence[ModelMessage],
        deferred: DeferredToolResults | None = None,
    ) -> AgentRunResult[AgentOutput]:
        try:
            result = await self._agent.run(
                prompt,
                deps=deps,
                message_history=list(message_history),
                deferred_tool_results=deferred,
                toolsets=[rezerve.toolset],
                usage_limits=self._limits,
            )
        except Exception as error:
            turn.meter.add_failure(error)
            raise
        turn.meter.add(result.usage, result.new_messages())
        return result
