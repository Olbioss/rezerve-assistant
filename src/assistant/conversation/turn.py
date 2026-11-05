"""One turn: everything a customer wrote since the last answer, answered once.

`process` claims the conversation's lease, sends whatever a crash left
unsent, then answers its unprocessed messages, oldest first, until none is
left. A burst — "merhaba", then "yarın yer var mı?" — is one batch, one run,
one answer. A batch's outcome is written in one transaction, and its replies
are sent after it and marked sent one by one, so a retry resends only what
never went out (at-least-once, spec §7.2).
"""

import dataclasses
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolResults,
    FallbackExceptionGroup,
    ModelAPIError,
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
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
    asked_about_a_second_booking,
    classify_by_lexicon,
    normalise,
    propose,
    refusal,
    resolution,
    write_actions,
    write_receipt,
)
from assistant.agent.budget import SLOW_DOWN, Budget
from assistant.agent.handoff import (
    HANDED_OFF,
    booking_page,
    handoff_reply,
    owner_notice,
    owner_unreachable,
    relayed,
    unavailable_reply,
)
from assistant.channels.base import Channel, ChannelName
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
    State,
    Store,
    StoredInbound,
    TurnResult,
)
from assistant.mcp import BusinessInfo, CompletedWrite, OpenRezerve, RezerveSession
from assistant.observability import tagged
from assistant.queue.base import TaskQueue
from assistant.reminders import MIN_NOTICE, REMIND_BEFORE, booked

log = logging.getLogger(__name__)
# A no-op until observability.configure sets an exporter.
tracer = trace.get_tracer("assistant.turn")

# Calls code may send back to the model in one turn — a service id it made up,
# an email it forgot to ask for — before the business is handed the customer.
MAX_REFUSALS = 2
# How long an owner keeps a conversation they were handed (spec §7.6).
OWNER_KEEPS = timedelta(hours=24)
# Messages the owner sees with a handoff.
RECENT = 6


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
    # Spec §7.5; the default has no limits, as tests and the evals want.
    budget: Budget = field(default_factory=Budget)

    @classmethod
    def from_settings(cls, settings: Settings) -> "TurnSettings":
        return cls(
            request_limit=settings.run_request_limit,
            token_limit=settings.run_token_limit,
            approval_ttl=timedelta(seconds=settings.approval_ttl_seconds),
            lease_ttl=timedelta(seconds=settings.lease_ttl_seconds),
            lease_renew=timedelta(seconds=settings.lease_renew_seconds),
            rezerve_mcp_url=settings.rezerve_mcp_url,
            budget=Budget(
                daily_requests=settings.daily_request_cap,
                conversation_daily_tokens=settings.conversation_daily_token_cap,
                sender_messages=settings.sender_message_limit,
                sender_window=timedelta(seconds=settings.sender_window_seconds),
            ),
        )


def _is_tool_error(part: ModelRequestPart) -> bool:
    """A call that failed on Rezerve, or whose arguments did not validate."""
    if isinstance(part, ToolReturnPart):
        return part.outcome == "failed"
    return isinstance(part, RetryPromptPart) and part.tool_name is not None


@dataclass
class Meter:
    """What a turn cost, across its agent runs and the reply classifier."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    models_tried: list[str] = field(default_factory=list[str])
    model_used: str | None = None

    def add(self, usage: RunUsage, messages: Sequence[ModelMessage]) -> None:
        self.requests += usage.requests
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.tool_calls += usage.tool_calls
        for message in messages:
            if isinstance(message, ModelRequest):
                self.tool_errors += sum(map(_is_tool_error, message.parts))
            if isinstance(message, ModelResponse):
                for attempt in message.failed_attempts or []:
                    # Otherwise invisible: a model that rejects every request
                    # looks like a healthy run on the next one in the chain.
                    log.warning("Model %s failed, fell back: %s", attempt.model_name, attempt.error)
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
    # The session's record of the writes that ran this turn.
    writes: list[CompletedWrite] = field(default_factory=list[CompletedWrite])
    # What refusals this turn had code tell the customer, and the word that
    # shows a reply already said it.
    told: list[tuple[str, str | None]] = field(default_factory=list[tuple[str, str | None]])
    _clock_start: float = field(default_factory=time.monotonic)

    def finish(
        self,
        replies: list[Reply],
        outcome: Outcome,
        *,
        messages: list[ModelMessage] | None = None,
        approval: NewApproval | None = None,
        owner_notes: list[str] | None = None,
        state: State | None = None,
    ) -> TurnResult:
        receipt = write_receipt(self.writes)
        if receipt is not None and outcome != "reply":
            # The model's own reply tells of the writes it made; any other
            # ending — a hand-off, a new proposal, a failure — would not, and
            # a booking made must never go unsaid or lose its payment button.
            replies = [Reply(receipt, write_actions(self.writes)), *replies]
        for note, unless in self.told:
            said = " ".join(normalise(reply.text) for reply in replies)
            if unless is None or normalise(unless) not in said:
                replies = [Reply(note), *replies]
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
            tool_errors=meter.tool_errors,
            outcome=outcome,
            trace_id=_current_trace_id(),
        )
        return TurnResult(
            processed=self.processed,
            replies=replies,
            history=history.trim(messages) if messages is not None else None,
            run=run,
            resolved=self.resolved,
            approval=approval,
            owner_notes=owner_notes or [],
            state=state,
            booked=booked(self.writes),
        )


def _current_trace_id() -> str | None:
    span = trace.get_current_span().get_span_context()
    return format(span.trace_id, "032x") if span.is_valid else None


def _describe(span: Span, result: TurnResult) -> None:
    """Spec §7.10: what the turn did, on its trace."""
    run = result.run
    if run is None:
        return
    span.set_attributes(
        {
            "turn.outcome": run.outcome,
            "turn.model_used": run.model_used or "",
            "turn.models_tried": run.models_tried,
            "turn.requests": run.requests,
            "turn.input_tokens": run.input_tokens,
            "turn.output_tokens": run.output_tokens,
        }
    )


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
        queue: TaskQueue | None = None,
    ) -> None:
        """`queue` takes the reminders of what a turn books; without one, none are kept."""
        self._store = store
        self._queue = queue
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
        with tracer.start_as_current_span("turn") as span:
            await self._process(conversation_id, span)

    async def _process(self, conversation_id: int, span: Span) -> None:
        settings = self._settings
        async with Lease(
            self._store,
            conversation_id,
            ttl=settings.lease_ttl,
            renew_every=settings.lease_renew,
        ) as lease:
            conversation = await self._store.conversation(conversation_id)
            trace_values = {
                "langfuse.session.id": str(conversation.id),
                "langfuse.user.id": conversation.customer_ref,
            }
            span.set_attributes(
                {
                    **trace_values,
                    "langfuse.trace.name": "turn",
                    "business.slug": conversation.business_slug,
                    "channel": conversation.channel,
                }
            )
            with tagged(trace_values):
                await self._answer_all(conversation, span, lease)

    async def _answer_all(self, conversation: ConversationRecord, span: Span, lease: Lease) -> None:
        """Every unprocessed message, oldest first, until none is left."""
        conversation = await self._send_unsent(conversation)
        while batch := await self._store.unprocessed_inbound(conversation.id):
            if await self._owner_has(conversation):
                result = self._relay(batch)
            else:
                result = await self._answer(conversation, batch, lease.check)
                if conversation.state == "human" and result.state is None:
                    # The owner's 24 hours are over, or no owner is linked now.
                    result = dataclasses.replace(result, state="active")
            lease.check()
            await self._store.commit_turn(conversation.id, result, self._clock())
            _describe(span, result)
            await self._remind(conversation, result.booked)
            await self._send_unsent(conversation)
            conversation = await self._store.conversation(conversation.id)

    async def _send_unsent(self, conversation: ConversationRecord) -> ConversationRecord:
        """Sends what waits unsent, the customer's first, then the owner's.
        Returns the conversation as it now stands: an owner Telegram refuses
        cannot answer, so the assistant takes the conversation back (spec §7.5)."""
        await self._send_to_customer(conversation)
        if await self._send_to_owner(conversation):
            return conversation
        page = booking_page(self._settings.rezerve_mcp_url, conversation.business_slug)
        if await self._store.hand_back(conversation.id, owner_unreachable(page)) is None:
            return conversation
        await self._send_to_customer(conversation)
        return await self._store.conversation(conversation.id)

    async def _send_to_customer(self, conversation: ConversationRecord) -> None:
        channel = self._channels[conversation.channel]
        for message in await self._store.unsent(conversation.id):
            sent_id = await channel.send(conversation.channel_user_id, message)
            await self._store.mark_sent(message.seq, sent_id, self._clock())

    async def _send_to_owner(self, conversation: ConversationRecord) -> bool:
        """Sends the owner's notes; False if Telegram refused one for good."""
        notes = await self._store.unsent_to_owner(conversation.id)
        if not notes:
            return True
        owner = await self._owner_chat(conversation.business_slug)
        telegram = self._channels.get("telegram")
        reached = True
        for note in notes:
            if owner is None or telegram is None:
                # Unlinked since it was written: there is no one to tell.
                await self._store.mark_sent(note.seq, "owner:unlinked", self._clock())
                continue
            sent_id = await telegram.send(str(owner), note)
            delivered = await self._store.mark_relayed(
                note.seq, sent_id, owner, conversation.id, self._clock()
            )
            reached = reached and delivered
        return reached

    async def _remind(
        self, conversation: ConversationRecord, booked: list[tuple[str, datetime]]
    ) -> None:
        """Spec §7.7: a reminder a day ahead of what this turn booked or moved,
        on Telegram, when that day ahead is still at least an hour away."""
        if self._queue is None or conversation.channel != "telegram":
            return
        now = self._clock()
        for booking_id, starts_at in booked:
            at = starts_at - REMIND_BEFORE
            if at - now < MIN_NOTICE:
                continue
            kept = await self._store.add_reminder(
                booking_id,
                starts_at,
                conversation.business_slug,
                conversation.customer_ref,
                int(conversation.channel_user_id),
                now,
            )
            if not kept:
                continue
            try:
                await self._queue.enqueue_reminder(booking_id, starts_at, at)
            except Exception:
                # The booking is made either way; only its reminder is lost.
                log.exception("Could not schedule the reminder for booking %s", booking_id)

    async def _owner_chat(self, business_slug: str) -> int | None:
        """The business owner's Telegram chat, if one is linked and Telegram is on."""
        if "telegram" not in self._channels:
            return None
        return await self._store.owner_chat(business_slug)

    async def _owner_has(self, conversation: ConversationRecord) -> bool:
        """Whether the owner is answering this conversation now (spec §7.6)."""
        handed_off_at = conversation.handed_off_at
        if conversation.state != "human" or handed_off_at is None:
            return False
        if handed_off_at <= self._clock() - OWNER_KEEPS:
            return False
        return await self._owner_chat(conversation.business_slug) is not None

    def _relay(self, batch: list[StoredInbound]) -> TurnResult:
        """The customer's words, passed to the owner: no model, no reply."""
        text = "\n".join(message.text for message in batch)
        return TurnResult(
            processed=[message.seq for message in batch], replies=[], owner_notes=[relayed(text)]
        )

    async def _hand_off(
        self,
        conversation: ConversationRecord,
        turn: "_Turn",
        business: BusinessInfo,
        reason: str,
        messages: list[ModelMessage] | None = None,
    ) -> TurnResult:
        """To the owner's chat when one is linked; otherwise the business's phone."""
        if await self._owner_chat(conversation.business_slug) is None:
            return turn.finish([Reply(handoff_reply(business))], "handoff", messages=messages)
        recent = await self._store.recent_messages(conversation.id, RECENT)
        notice = owner_notice(conversation.business_slug, reason, recent)
        return turn.finish(
            [Reply(HANDED_OFF)], "handoff", messages=messages, owner_notes=[notice], state="human"
        )

    async def _answer(
        self,
        conversation: ConversationRecord,
        batch: list[StoredInbound],
        check_lease: Callable[[], None],
    ) -> TurnResult:
        turn = _Turn(processed=[message.seq for message in batch], started_at=self._clock())
        budget = self._settings.budget
        spent = await budget.spent(self._store, conversation, self._clock())
        if spent == "sender_rate":
            # Spec §7.5: told once, then silence until the sender slows down.
            since = self._clock() - budget.sender_window
            told = await self._store.said_since(conversation.id, SLOW_DOWN, since)
            return turn.finish([] if told else [Reply(SLOW_DOWN)], "degraded")
        if spent is not None:
            # No model call, and still an answer (spec §7.5).
            log.warning("Conversation %s: the %s budget is spent", conversation.id, spent)
            return turn.finish([Reply(self._unavailable(conversation, turn))], "degraded")
        try:
            rezerve = self._open_rezerve(conversation.business_slug, conversation.customer_ref)
            # The session records each write as it runs: kept on the turn, so
            # even an ending by failure tells the customer what was done.
            turn.writes = rezerve.completed_writes
            async with rezerve:
                turn.business = await rezerve.business_info()
                deps = TurnDeps(
                    business=turn.business,
                    services=await rezerve.services(),
                    bookings=await rezerve.my_bookings(),
                )
                return await self._converse(conversation, batch, deps, rezerve, turn, check_lease)
        except LeaseLost:
            raise
        except UsageLimitExceeded:
            if turn.business is None:
                raise
            return await self._hand_off(
                conversation, turn, turn.business, "Asistan bu sohbette istek sınırına ulaştı."
            )
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
        batch: list[StoredInbound],
        deps: TurnDeps,
        rezerve: RezerveSession,
        turn: _Turn,
        check_lease: Callable[[], None],
    ) -> TurnResult:
        text = "\n".join(message.text for message in batch)
        approval = await self._store.open_approval(conversation.id)
        # Asked in an earlier turn only: a retry straight after the refusal
        # must not count as the customer having answered.
        asked = asked_about_a_second_booking(conversation.history) or (
            approval is not None and asked_about_a_second_booking(approval.history)
        )
        if approval is None:
            run = await self._run(
                deps, rezerve, turn, text, history.close_open_calls(conversation.history)
            )
        else:
            # Only what was written after the confirmation can answer it: a
            # "tamam" typed while it was being prepared never saw what it
            # would approve. (Rows older than migration 0002 have no boundary.)
            boundary = approval.confirmation_seq
            answers = [m.text for m in batch if boundary is None or m.seq > boundary]
            if not answers:
                # The confirmation stays open, still the latest thing on screen.
                return turn.finish([], "confirmation")
            decision = await self._decide(approval, "\n".join(answers), turn)
            if decision == "approve":
                # The write runs inside the next call: never on a lapsed lease.
                check_lease()
            # Spent from here on, whatever the run below does.
            turn.resolved = (approval.id, _decision(decision))
            run = await self._run(
                deps, rezerve, turn, text, approval.history, resolution(approval, decision)
            )
        for _ in range(MAX_REFUSALS + 1):
            output = run.output
            if isinstance(output, Handoff):
                return await self._hand_off(
                    conversation, turn, deps.business, output.reason, run.all_messages()
                )
            if isinstance(output, str):
                if not output.strip():
                    # An empty answer is no answer: send the booking page instead.
                    reply = Reply(self._unavailable(conversation, turn))
                    return turn.finish([reply], "degraded", messages=run.all_messages())
                reply = Reply(output, write_actions(turn.writes))
                return turn.finish([reply], "reply", messages=run.all_messages())
            first, *others = output.approvals
            proposal = await propose(first, deps, rezerve, self._clock(), asked=asked)
            if isinstance(proposal, Refusal):
                turn.meter.tool_errors += 1
                if proposal.tell is not None:
                    turn.told.append((proposal.tell, proposal.unless))
                run = await self._run(
                    deps, rezerve, turn, None, run.all_messages(), refusal(output, proposal.reason)
                )
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
        return await self._hand_off(
            conversation,
            turn,
            deps.business,
            "Asistan isteği tamamlayamadı.",
            history.close_open_calls(run.all_messages()),
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
