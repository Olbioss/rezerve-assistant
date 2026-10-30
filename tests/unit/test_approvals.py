from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from jsonschema import Draft202012Validator
from pydantic_ai import ToolApproved, ToolCallPart, ToolDenied

from assistant.agent.agent import TurnDeps
from assistant.agent.approvals import (
    DENIED,
    EXPIRED,
    ONE_AT_A_TIME,
    Proposal,
    Refusal,
    classify_by_lexicon,
    normalise,
    propose,
    resolution,
    write_actions,
    write_receipt,
)
from assistant.agent.format import local_label
from assistant.conversation.store import OpenApproval
from assistant.mcp import CompletedWrite, RezerveError, RezerveSession
from tests.fakes.rezerve import CILT, SAC, TIMEZONE, FakeRezerve, snapshot_tools

NOW = datetime.now(UTC)
QUESTION = "Onaylıyor musunuz? (evet / hayır)"


@pytest.mark.parametrize(
    "text", ["evet", "Evet.", "EVET", "tamam", "Olur!", "👍", "onaylıyorum", "ONAYLIYORUM"]
)
def test_a_plain_yes_approves(text: str) -> None:
    assert classify_by_lexicon(text, "create_booking") == "approve"


@pytest.mark.parametrize("text", ["hayır", "HAYIR", "Vazgeçtim", "👎", "istemiyorum", "İPTAL"])
def test_a_plain_no_denies(text: str) -> None:
    assert classify_by_lexicon(text, "create_booking") == "deny"


def test_iptal_is_left_to_the_classifier_when_cancelling() -> None:
    assert classify_by_lexicon("iptal", "cancel_booking") is None


@pytest.mark.parametrize("text", ["evet ama 16:00 olsun", "15:30 değil 16:00", "hmm", "evetttt"])
def test_anything_else_is_left_to_the_classifier(text: str) -> None:
    assert classify_by_lexicon(text, "create_booking") is None


def test_capitals_lower_the_turkish_way() -> None:
    assert normalise("İSTANBUL'DA IŞIK!") == "istanbulda ışık"


@pytest.fixture
def fake() -> FakeRezerve:
    return FakeRezerve()


@pytest.fixture
async def rezerve(fake: FakeRezerve) -> AsyncIterator[RezerveSession]:
    async with fake.session() as session:
        yield session


@pytest.fixture
async def deps(rezerve: RezerveSession) -> TurnDeps:
    return TurnDeps(
        business=await rezerve.business_info(),
        services=await rezerve.services(),
        bookings=[],
    )


def create(**args: object) -> ToolCallPart:
    return ToolCallPart("create_booking", args, tool_call_id="c1")


async def test_a_booking_with_kapora_reads_back_the_exact_terms(
    deps: TurnDeps, rezerve: RezerveSession
) -> None:
    starts_at = "2026-10-17T12:30:00Z"
    call = create(
        service_id=CILT,
        starts_at=starts_at,
        customer_name=" Elif Kaya ",
        customer_email="elif@example.com",
    )
    assert await propose(call, deps, rezerve, NOW) == Proposal(
        text="Elif Kaya adına: Cilt Bakımı · Cumartesi 17 Ekim 15:30 · 60 dk · "
        f"₺300 kapora (ödeme linki 30 dk geçerli). {QUESTION}",
        args={
            "service_id": CILT,
            "starts_at": starts_at,
            "customer_name": "Elif Kaya",
            "customer_email": "elif@example.com",
            "expect_deposit_cents": 30_000,
        },
    )


async def test_a_booking_without_kapora_shows_the_price(
    deps: TurnDeps, rezerve: RezerveSession
) -> None:
    call = create(service_id=SAC, starts_at="2026-10-17T12:30:00Z", customer_name="Ali")
    proposal = await propose(call, deps, rezerve, NOW)
    assert isinstance(proposal, Proposal)
    assert (
        proposal.text
        == f"Ali adına: Saç Kesimi · Cumartesi 17 Ekim 15:30 · 45 dk · ₺800. {QUESTION}"
    )
    assert proposal.args["expect_deposit_cents"] is None


async def test_a_second_booking_of_the_same_service_is_asked_about_first(
    fake: FakeRezerve, rezerve: RezerveSession
) -> None:
    # "Perşembe 15:00'e saç kesimi alabilir miyim?", from a customer with a
    # haircut on Tuesday, may mean a move: the confirmation asks before a
    # second booking is written.
    tuesday = datetime(2026, 10, 20, 8, 30, tzinfo=UTC)
    fake.add_booking(SAC, tuesday)
    deps = TurnDeps(
        business=await rezerve.business_info(),
        services=await rezerve.services(),
        bookings=await rezerve.my_bookings(),
    )
    call = create(service_id=SAC, starts_at="2026-10-22T12:00:00Z", customer_name="Elif Kaya")
    stays = local_label(tuesday, TIMEZONE)
    # Not asked yet: back to the model, to ask whether this is a move.
    first = await propose(call, deps, rezerve, NOW)
    assert isinstance(first, Refusal)
    assert f"Saç Kesimi · {stays}" in first.reason
    assert "Yeni bir randevu mu istediğini" in first.reason
    # Asked: the confirmation still names the booking that stays.
    proposal = await propose(call, deps, rezerve, NOW, asked=True)
    assert isinstance(proposal, Proposal)
    assert proposal.text == (
        "Elif Kaya adına: Saç Kesimi · Perşembe 22 Ekim 15:00 · 45 dk · ₺800. "
        f"Bu ikinci bir randevu olur; duran randevunuz: Saç Kesimi · {stays}. "
        f"Onu taşımak isterseniz söyleyin. {QUESTION}"
    )


async def test_a_booking_of_another_service_needs_no_word_about_the_first(
    fake: FakeRezerve, rezerve: RezerveSession
) -> None:
    fake.add_booking(CILT, datetime(2026, 10, 20, 8, 30, tzinfo=UTC))
    deps = TurnDeps(
        business=await rezerve.business_info(),
        services=await rezerve.services(),
        bookings=await rezerve.my_bookings(),
    )
    call = create(service_id=SAC, starts_at="2026-10-22T12:00:00Z", customer_name="Elif Kaya")
    proposal = await propose(call, deps, rezerve, NOW)
    assert isinstance(proposal, Proposal)
    assert "ikinci" not in proposal.text


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (
            {"service_id": "nope", "starts_at": "2026-10-17T12:30:00Z", "customer_name": "Ali"},
            "hizmet",
        ),
        ({"service_id": SAC, "starts_at": "cumartesi", "customer_name": "Ali"}, "starts_at"),
        (
            {"service_id": SAC, "starts_at": "2026-10-17T15:30:00", "customer_name": "Ali"},
            "starts_at",
        ),
        ({"service_id": SAC, "starts_at": "2026-10-17T12:30:00Z"}, "adını"),
        # A name the model wrote because it never asked: seen in a live eval run.
        (
            {"service_id": SAC, "starts_at": "2026-10-17T12:30:00Z", "customer_name": "Müşteri"},
            "adını",
        ),
        (
            {"service_id": SAC, "starts_at": "2026-10-17T12:30:00Z", "customer_name": "misafir"},
            "adını",
        ),
        (
            {"service_id": CILT, "starts_at": "2026-10-17T12:30:00Z", "customer_name": "Ali"},
            "e-posta",
        ),
    ],
)
async def test_a_booking_code_cannot_confirm_goes_back_to_the_model(
    deps: TurnDeps, rezerve: RezerveSession, args: dict[str, object], reason: str
) -> None:
    refused = await propose(create(**args), deps, rezerve, NOW)
    assert isinstance(refused, Refusal)
    assert reason in refused.reason


async def test_a_move_names_both_times(
    fake: FakeRezerve, deps: TurnDeps, rezerve: RezerveSession
) -> None:
    booking = fake.add_booking(SAC, NOW + timedelta(days=2))
    new_start = NOW + timedelta(days=3)
    call = ToolCallPart(
        "reschedule_booking",
        {"booking_id": booking.id, "new_starts_at": new_start.isoformat()},
        tool_call_id="c1",
    )
    proposal = await propose(call, deps, rezerve, NOW)
    assert isinstance(proposal, Proposal)
    assert proposal.text == (
        f"Saç Kesimi randevunuz {local_label(booking.starts_at, TIMEZONE)} → "
        f"{local_label(new_start, TIMEZONE)} olarak değişecek. {QUESTION}"
    )
    assert proposal.args == call.args_as_dict()


async def test_an_unpaid_hold_or_a_paid_booking_inside_a_day_is_not_moved(
    fake: FakeRezerve, deps: TurnDeps, rezerve: RezerveSession
) -> None:
    hold = fake.add_booking(CILT, NOW + timedelta(days=2), status="pending")
    soon = fake.add_booking(CILT, NOW + timedelta(hours=5), paid=True)
    for booking in (hold, soon):
        call = ToolCallPart(
            "reschedule_booking",
            {"booking_id": booking.id, "new_starts_at": (NOW + timedelta(days=4)).isoformat()},
            tool_call_id="c1",
        )
        assert isinstance(await propose(call, deps, rezerve, NOW), Refusal)


async def test_an_unpaid_hold_refused_a_move_carries_a_word_for_the_customer(
    fake: FakeRezerve, deps: TurnDeps, rezerve: RezerveSession
) -> None:
    hold = fake.add_booking(CILT, NOW + timedelta(days=2), status="pending")
    call = ToolCallPart(
        "reschedule_booking",
        {"booking_id": hold.id, "new_starts_at": (NOW + timedelta(days=4)).isoformat()},
        tool_call_id="c1",
    )
    refused = await propose(call, deps, rezerve, NOW)
    assert isinstance(refused, Refusal)
    assert refused.tell == "Bu randevunun kaporası henüz ödenmediği için taşınamıyor."
    assert refused.unless == "kapora"


@pytest.mark.parametrize(
    ("hours_ahead", "paid", "sentence", "refund"),
    [
        (48, True, " Kaporanız iade edilecek.", True),
        (5, True, " 24 saatten az kaldığı için kapora işletmede kalacak.", False),
        (48, False, "", False),
    ],
)
async def test_a_cancellation_states_what_happens_to_the_kapora(
    fake: FakeRezerve,
    deps: TurnDeps,
    rezerve: RezerveSession,
    hours_ahead: int,
    paid: bool,
    sentence: str,
    refund: bool,
) -> None:
    booking = fake.add_booking(CILT, NOW + timedelta(hours=hours_ahead), paid=paid)
    call = ToolCallPart("cancel_booking", {"booking_id": booking.id}, tool_call_id="c1")
    proposal = await propose(call, deps, rezerve, NOW)
    assert isinstance(proposal, Proposal)
    label = local_label(booking.starts_at, TIMEZONE)
    assert proposal.text == f"Cilt Bakımı · {label} randevunuz iptal edilecek.{sentence} {QUESTION}"
    assert proposal.args == {"booking_id": booking.id, "expect_refund": refund}


async def test_someone_elses_or_a_cancelled_booking_is_refused(
    fake: FakeRezerve, deps: TurnDeps, rezerve: RezerveSession
) -> None:
    cancelled = fake.add_booking(SAC, NOW + timedelta(days=2), status="cancelled")
    for booking_id in ("44444444-4444-4444-8444-444444444444", cancelled.id):
        call = ToolCallPart("cancel_booking", {"booking_id": booking_id}, tool_call_id="c1")
        assert isinstance(await propose(call, deps, rezerve, NOW), Refusal)


async def test_a_write_this_service_cannot_confirm_is_refused(
    deps: TurnDeps, rezerve: RezerveSession
) -> None:
    call = ToolCallPart("delete_business", {}, tool_call_id="c1")
    assert isinstance(await propose(call, deps, rezerve, NOW), Refusal)


def approval(other_call_ids: list[str]) -> OpenApproval:
    return OpenApproval(
        id=1,
        tool_call_id="c1",
        tool_name="create_booking",
        args={"service_id": CILT, "expect_deposit_cents": 30_000},
        other_call_ids=other_call_ids,
        rendered_text="…",
        history=[],
        expires_at=NOW,
    )


def test_a_yes_runs_the_pinned_arguments_and_nothing_else() -> None:
    results = resolution(approval(["c2"]), "approve")
    assert results.approvals == {
        "c1": ToolApproved(override_args={"service_id": CILT, "expect_deposit_cents": 30_000}),
        "c2": ToolDenied(ONE_AT_A_TIME),
    }


@pytest.mark.parametrize(
    ("decision", "message"), [("deny", DENIED), ("other", DENIED), ("expired", EXPIRED)]
)
def test_anything_but_a_yes_runs_nothing(decision: str, message: str) -> None:
    results = resolution(approval([]), decision)  # pyright: ignore[reportArgumentType]
    assert results.approvals == {"c1": ToolDenied(message)}


async def test_every_proposal_is_a_call_rezerve_accepts(
    fake: FakeRezerve, deps: TurnDeps, rezerve: RezerveSession
) -> None:
    """The pinned arguments fit the contract (contract/rezerve-mcp-tools.json)."""
    inputs = {tool["name"]: tool["inputSchema"] for tool in snapshot_tools()}
    booking = fake.add_booking(CILT, NOW + timedelta(days=2), paid=True)
    calls = [
        create(service_id=SAC, starts_at="2026-10-17T12:30:00Z", customer_name="Ali"),
        ToolCallPart(
            "reschedule_booking",
            {"booking_id": booking.id, "new_starts_at": (NOW + timedelta(days=3)).isoformat()},
        ),
        ToolCallPart("cancel_booking", {"booking_id": booking.id}),
    ]
    for call in calls:
        proposal = await propose(call, deps, rezerve, NOW)
        assert isinstance(proposal, Proposal), call.tool_name
        Draft202012Validator(inputs[call.tool_name]).validate(proposal.args)


@pytest.mark.parametrize(
    ("write", "receipt"),
    [
        (
            CompletedWrite(
                "create_booking",
                result={"status": "confirmed", "local_label": "Cuma 16 Ekim 14:00"},
            ),
            "Randevunuz alındı: Cuma 16 Ekim 14:00.",
        ),
        (
            CompletedWrite("reschedule_booking", result={"local_label": "Cuma 16 Ekim 16:00"}),
            "Randevunuz Cuma 16 Ekim 16:00 olarak değiştirildi.",
        ),
        (
            CompletedWrite("cancel_booking", result={"deposit_refunded": True}),
            "Randevunuz iptal edildi. Kaporanız iade edildi.",
        ),
        (
            CompletedWrite("cancel_booking", result={"deposit_kept": True}),
            "Randevunuz iptal edildi. Kapora işletmede kaldı.",
        ),
        (
            CompletedWrite("cancel_booking", result={"refund_failed": True}),
            "Randevunuz iptal edildi. Kapora iadesi başlatılamadı; işletme ilgilenecek.",
        ),
        (
            CompletedWrite(
                "create_booking", refusal=RezerveError("slot_taken", "Bu saat az önce doldu.")
            ),
            "İşlem yapılamadı: Bu saat az önce doldu.",
        ),
    ],
)
def test_a_receipt_says_what_each_write_did(write: CompletedWrite, receipt: str) -> None:
    assert write_receipt([write]) == receipt


def test_no_write_no_receipt_and_only_a_checkout_makes_a_button() -> None:
    assert write_receipt([]) is None
    confirmed = CompletedWrite(
        "create_booking", result={"status": "confirmed", "checkout_url": None}
    )
    held = CompletedWrite(
        "create_booking", result={"status": "pending", "checkout_url": "https://pay.example/1"}
    )
    assert write_actions([confirmed]) == []
    assert write_actions([confirmed, held]) == [
        {"type": "link", "label": "Kaporayı öde", "url": "https://pay.example/1"}
    ]
