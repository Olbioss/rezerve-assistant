"""One scenario, start to finish, in process (spec §8.2).

Rezerve's eval business is reset and seeded; the customer's messages go
through the same TurnProcessor the service runs, one at a time, each answered
before the next is written; then Rezerve's bookings and the transcript are
graded. Channels and HTTP stay out of the loop: integration tests cover them.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from uuid import uuid4

from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
from assistant.conversation.store import Store
from assistant.conversation.turn import TurnProcessor
from assistant.identity import customer_ref
from evals.graders import Expected, Happened, Line, Window, grade
from evals.judge import Judge
from evals.models import PatientModel
from evals.results import CheckResult, LineRecord, ScenarioResult, TurnRecord
from evals.rezerve_side import BookingRow, Business, NewBooking, RezerveSide
from evals.scenario import Scenario, Seed, When
from evals.simulate import Simulator
from evals.when import Clock, combine, parse_time, render, resolve, resolve_day

EVAL_SLUG = "eval"


class Unbookable(Exception):
    """The scenario names a time Rezerve would not book today: it is skipped."""


@dataclass
class Outbox:
    """The eval channel: whatever the assistant sends is kept to be read."""

    name: ChannelName = "web"
    sent: list[OutboundMessage] = field(default_factory=list[OutboundMessage])

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        self.sent.append(message)
        return f"eval:{message.seq}"


@dataclass
class Rig:
    """Everything a scenario runs against, built once per run."""

    store: Store
    processor: TurnProcessor
    outbox: Outbox
    rezerve: RezerveSide
    secret: str
    simulator: Simulator | None = None
    judge: Judge | None = None
    # Every model of the run, made patient: the first to run out stops it.
    patience: list[PatientModel] = field(default_factory=list[PatientModel])

    def check_quota(self) -> None:
        for model in self.patience:
            if model.exhausted is not None:
                raise model.exhausted

    def waited(self) -> float:
        return sum(model.waited for model in self.patience)


@dataclass(frozen=True)
class Plan:
    """A scenario resolved against today and the eval business."""

    seeds: list[NewBooking]
    # Seeded the first time the assistant asks the customer to confirm.
    meanwhile: list[NewBooking]
    created: list[Window]
    moved: dict[str, Window]
    cancelled: set[str]
    # Other customers' names and phones: the assistant must never say them.
    private: list[str]

    def expected(self, scenario: Scenario, ids: dict[str, str]) -> Expected:
        return Expected(
            created=self.created,
            moved={ids[key]: window for key, window in self.moved.items()},
            cancelled={ids[key] for key in self.cancelled},
            handoff=scenario.expect.handoff,
            says=scenario.expect.says,
            never_says=scenario.expect.never_says,
            writes=None if scenario.expect.writes is None else list(scenario.expect.writes),
        )

    def described(self, zone: tzinfo) -> list[str]:
        """Where the bookings should land, the way the business sees them."""
        return [_describe(window, zone) for window in [*self.created, *self.moved.values()]]


def _describe(window: Window, zone: tzinfo) -> str:
    start, end = window.earliest.astimezone(zone), window.latest.astimezone(zone)
    at = f"{start:%H:%M}" if start == end else f"{start:%H:%M}–{end:%H:%M}"
    return " ".join(part for part in (window.service, f"{start:%a %d %b}", at) if part)


def plan(scenario: Scenario, business: Business, clock: Clock, me: str, other: str) -> Plan:
    def window(
        when: When, service: str | None, status: str | None, name: str | None = None
    ) -> Window:
        first, last = (when.at, when.at) if when.at else when.between or ("", "")
        day = resolve_day(when.day, clock, parse_time(last))
        earliest, latest = (
            combine(day, parse_time(first), clock),
            combine(day, parse_time(last), clock),
        )
        _bookable(business, clock, latest, service)
        return Window(earliest, latest, service, status, name)

    def booking(seed: Seed) -> NewBooking:
        starts_at = resolve(seed.day, seed.at, clock)
        _bookable(business, clock, starts_at, seed.service)
        return NewBooking(
            key=seed.key,
            service=seed.service,
            starts_at=starts_at,
            customer_ref=me if seed.customer == "me" else other,
            name=seed.name,
            phone=seed.phone,
            status=seed.status,
            paid=seed.paid,
        )

    expect = scenario.expect
    return Plan(
        seeds=[booking(seed) for seed in scenario.seed],
        meanwhile=[booking(seed) for seed in scenario.meanwhile],
        created=[
            window(booking, booking.service, booking.status, booking.name)
            for booking in expect.bookings
        ],
        moved={move.key: window(move, None, None) for move in expect.moved},
        cancelled=set(expect.cancelled),
        private=private_details(scenario),
    )


def private_details(scenario: Scenario) -> list[str]:
    """Other customers' names and phones: the assistant must never say them."""
    return [
        text
        for seed in scenario.seed + scenario.meanwhile
        if seed.customer == "other"
        for text in (seed.name, seed.phone)
        if text
    ]


def _bookable(business: Business, clock: Clock, starts_at: datetime, service: str | None) -> None:
    """Raise Unbookable unless Rezerve would offer `starts_at` right now."""
    local = starts_at.astimezone(clock.now.tzinfo)
    if starts_at < clock.now + clock.min_lead:
        raise Unbookable(f"{local:%a %d %b %H:%M} is too soon")
    if local.date() > clock.today + timedelta(days=business.booking_window_days):
        raise Unbookable(f"{local:%a %d %b} is past the booking window")
    duration = business.services[service].duration_minutes if service else 0
    start = local.hour * 60 + local.minute
    spans = business.hours.get(local.weekday(), [])
    if not any(open_ <= start and start + duration <= close for open_, close in spans):
        raise Unbookable(f"the business is closed at {local:%a %d %b %H:%M}")


def allowed_amounts(business: Business) -> set[int]:
    """Prices and kaporas, and what is left to pay after a kapora."""
    amounts: set[int] = set()
    for service in business.services.values():
        amounts.add(service.price_cents)
        if service.deposit_cents:
            amounts |= {service.deposit_cents, service.price_cents - service.deposit_cents}
    return amounts


async def run_scenario(
    scenario: Scenario, rig: Rig, attempt: str, now: datetime | None = None, trial: int = 1
) -> ScenarioResult:
    """Every call is a new customer, unknown to Rezerve and to the store, even
    under an `attempt` seen before: a resumed run repeats the scenario a quota
    cut off, and must not carry on the conversation the cut left behind."""
    business = await rig.rezerve.business()
    clock = Clock.at(now or datetime.now(UTC), business.timezone, business.min_lead)
    customer = f"{scenario.id}.{attempt}.{uuid4().hex[:8]}"
    me = customer_ref(rig.secret, "eval", customer)
    try:
        resolved = plan(scenario, business, clock, me, customer_ref(rig.secret, "eval", "other"))
    except Unbookable as reason:
        return ScenarioResult(
            id=scenario.id,
            tags=list(scenario.tags),
            mode=scenario.mode,
            status="skipped",
            reason=str(reason),
            started_at=clock.now,
        )

    await rig.rezerve.reset(scenario.business_notes)
    ids = await rig.rezerve.seed(resolved.seeds)
    before = await rig.rezerve.snapshot()
    talk = await _converse(scenario, rig, customer, me, clock, resolved.meanwhile, trial)
    lines, conversation_id = talk.lines, talk.conversation_id
    after = await rig.rezerve.snapshot()
    runs = await rig.store.runs(conversation_id) if conversation_id is not None else []
    decisions = await rig.store.decisions(conversation_id) if conversation_id is not None else []

    graded = grade(
        resolved.expected(scenario, ids),
        # Someone else's booking made meanwhile was there before the assistant
        # wrote anything that could clash with it.
        before + talk.seeded,
        after,
        Happened(lines, any(run.outcome == "handoff" for run in runs), decisions, talk.finished),
        me=me,
        timezone=business.timezone,
        private=resolved.private,
        amounts=allowed_amounts(business),
    )
    score = await rig.judge.score(lines) if rig.judge and "judge" in scenario.tags else None
    # One customer message, one turn: each turn's waits are the ones its message saw.
    waits = talk.waits if len(talk.waits) == len(runs) else [0.0] * len(runs)
    return ScenarioResult(
        id=scenario.id,
        tags=list(scenario.tags),
        mode=scenario.mode,
        status="pass" if graded.passed else "fail",
        started_at=clock.now,
        expected=resolved.described(clock.now.tzinfo or UTC),
        customer=customer,
        conversation_id=conversation_id,
        checks=[CheckResult(**asdict(check)) for check in graded.checks],
        dates_right=graded.dates_right,
        customer_turns=sum(1 for line in lines if line.speaker == "customer"),
        turns=[
            TurnRecord(
                **{k: v for k, v in asdict(run).items() if k != "started_at"},
                waited_ms=round(waited * 1000),
            )
            for run, waited in zip(runs, waits, strict=True)
        ],
        judge=score,
        transcript=[LineRecord(speaker=line.speaker, text=line.text) for line in lines],
    )


@dataclass(frozen=True)
class Talk:
    lines: list[Line]
    conversation_id: int | None
    # Whether the customer got to say they were done.
    finished: bool
    # Seconds of rate-limit waiting behind each customer message's turn.
    waits: list[float]
    # The bookings made meanwhile, as they were seeded.
    seeded: list[BookingRow]


async def _converse(
    scenario: Scenario,
    rig: Rig,
    customer: str,
    me: str,
    clock: Clock,
    meanwhile: list[NewBooking],
    trial: int = 1,
) -> Talk:
    lines: list[Line] = []
    waits: list[float] = []
    seeded: list[BookingRow] = []
    conversation_id: int | None = None
    for turn in range(scenario.expect.max_turns):
        if scenario.mode == "scripted":
            if turn == len(scenario.turns):
                return Talk(lines, conversation_id, True, waits, seeded)
            text: str | None = render(scenario.turns[turn], clock)
        else:
            assert rig.simulator is not None, "a simulated scenario needs the simulator"
            text = await rig.simulator.next_message(scenario, lines, clock.today, trial)
            if text is None:
                return Talk(lines, conversation_id, True, waits, seeded)
        lines.append(Line("customer", text))
        message = InboundMessage(EVAL_SLUG, "web", customer, str(uuid4()), text)
        recorded = await rig.store.record_inbound(message, me, datetime.now(UTC))
        conversation_id = recorded.conversation_id
        sent, waited = len(rig.outbox.sent), rig.waited()
        await rig.processor.process(conversation_id)
        waits.append(rig.waited() - waited)
        rig.check_quota()
        for reply in rig.outbox.sent[sent:]:
            buttons = "".join(f"\n[{action['label']}]" for action in reply.actions)
            lines.append(Line("assistant", reply.text + buttons))
        if meanwhile and not seeded and await rig.store.open_approval(conversation_id):
            ids = set((await rig.rezerve.seed(meanwhile)).values())
            seeded = [row for row in await rig.rezerve.snapshot() if row.id in ids]
    # Out of turns: a script has said everything; a simulated customer had more to say.
    return Talk(lines, conversation_id, scenario.mode == "scripted", waits, seeded)
