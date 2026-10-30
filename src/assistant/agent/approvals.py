"""Confirm before every write (spec §7.3).

A write never runs on the model's say-so. When the model calls one, code
renders the confirmation from the exact arguments that would run, with fresh
data from Rezerve, and the customer's next message decides. The terms the
customer is shown — the kapora, whether it is refunded — are pinned into
those arguments, so if they changed meanwhile Rezerve refuses the write
(`terms_changed`, `refund_window_closed`) instead of surprising anyone.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolRequests,
    DeferredToolResults,
    ModelMessage,
    ModelRequest,
    ToolApproved,
    ToolCallPart,
    ToolDenied,
    ToolReturnPart,
)
from pydantic_ai.models import Model

from assistant.agent.agent import TurnDeps
from assistant.agent.format import format_money, local_label
from assistant.channels.base import Action
from assistant.conversation.store import OpenApproval
from assistant.mcp import BookingView, CompletedWrite, RezerveError, RezerveSession

type ReplyDecision = Literal["approve", "deny", "other"]
type Resolution = Literal["approve", "deny", "other", "expired"]

APPROVE = frozenset({"evet", "olur", "tamam", "tamamdır", "onaylıyorum", "uygun", "ok", "👍"})
DENY = frozenset({"hayır", "vazgeçtim", "iptal", "istemiyorum", "👎"})
THUMBS = frozenset({"👍", "👎"})
# What a model writes as a name it never asked for: the booking goes back to it.
PLACEHOLDER_NAMES = frozenset(
    {"müşteri", "misafir", "isim", "ad", "ad soyad", "adı soyadı", "anonim", "customer", "guest"}
)

QUESTION = "Onaylıyor musunuz? (evet / hayır)"
DENIED = "Müşteri bu işlemi onaylamadı; işlem yapılmadı."
EXPIRED = (
    "Müşteri 10 dakika içinde onaylamadığı için işlem yapılmadı. "
    "Yeniden önermeden önce müsaitliği tekrar kontrol et."
)
ONE_AT_A_TIME = (
    "Aynı anda yalnızca bir değişiklik onaylanabilir; bu işlem yapılmadı. Gerekirse ayrıca öner."
)
# A new booking of a service the customer already has coming up may be a move.
_NEW_OR_MOVE = "Yeni bir randevu mu istediğini, yoksa o randevuyu mu taşımak istediğini sor"
SECOND_BOOKING = (
    "Müşterinin bu hizmette yaklaşan bir randevusu var: {booking}. "
    f"{_NEW_OR_MOVE}; taşımak isterse reschedule_booking kullan."
)

CLASSIFIER_INSTRUCTIONS = """\
Bir randevu asistanı müşteriye bir işlem için onay sordu. Müşterinin cevabını sınıflandır:
- approve: işlemi açıkça onaylıyor;
- deny: işlemi açıkça istemiyor;
- other: başka bir şey istiyor, bir değişiklik öneriyor ya da cevabı belirsiz."""


def normalise(text: str) -> str:
    """Lowercase the Turkish way — "I" is "ı" and "İ" is "i", which str.lower()
    gets wrong — keeping only words and the two thumbs."""
    lowered = text.replace("I", "ı").replace("İ", "i").lower()
    kept = "".join(ch for ch in lowered if ch.isalnum() or ch.isspace() or ch in THUMBS)
    return " ".join(kept.split())


def classify_by_lexicon(text: str, tool_name: str) -> ReplyDecision | None:
    """A whole-message yes or no; anything else is for the classifier."""
    reply = normalise(text)
    if reply in APPROVE:
        return "approve"
    if reply in DENY:
        # Answering "…randevunuz iptal edilecek. Onaylıyor musunuz?", "iptal" may well mean yes.
        if reply == "iptal" and tool_name == "cancel_booking":
            return None
        return "deny"
    return None


class ReplyClassifier:
    """The fallback for a reply the lexicon cannot read: one small structured call."""

    def __init__(self, model: Model) -> None:
        self._agent = Agent(
            model,
            output_type=Literal["approve", "deny", "other"],
            instructions=CLASSIFIER_INSTRUCTIONS,
        )

    async def classify(self, question: str, answer: str) -> AgentRunResult[ReplyDecision]:
        return await self._agent.run(f"Soru: {question}\nCevap: {answer}")


@dataclass(frozen=True)
class Proposal:
    """A confirmation for the customer, and the arguments that will run on a yes."""

    text: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Refusal:
    """Why a call could not even be proposed, told to the model. `tell` is said
    to the customer as well, whatever the model goes on to say, unless one of
    its replies already uses the word `unless`."""

    reason: str
    tell: str | None = None
    unless: str | None = None


def asked_about_a_second_booking(history: Sequence[ModelMessage]) -> bool:
    """Whether the model was already sent back, in this conversation, to ask
    whether a second booking of the same service was meant as a move."""
    return any(
        isinstance(part, ToolReturnPart) and _NEW_OR_MOVE in str(part.content)
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


async def propose(
    call: ToolCallPart,
    deps: TurnDeps,
    rezerve: RezerveSession,
    now: datetime,
    *,
    asked: bool = False,
) -> Proposal | Refusal:
    """`asked`: the customer has been asked, in an earlier turn, whether a
    second booking of the same service is a move."""
    args = call.args_as_dict()
    if call.tool_name == "create_booking":
        return _propose_create(args, deps, asked)
    if call.tool_name == "reschedule_booking":
        return await _propose_reschedule(args, deps, rezerve, now)
    if call.tool_name == "cancel_booking":
        return await _propose_cancel(args, deps, rezerve, now)
    # A write Rezerve added that this service does not know how to confirm.
    return Refusal(f"{call.tool_name} bu sohbette kullanılamaz.")


def _instant(value: Any) -> datetime | None:
    try:
        instant = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _propose_create(args: dict[str, Any], deps: TurnDeps, asked: bool) -> Proposal | Refusal:
    service = next((s for s in deps.services if s.id == args.get("service_id")), None)
    if service is None:
        return Refusal(
            "Bu hizmet bulunamadı; hizmet listesindeki service_id değerlerinden birini kullan."
        )
    starts_at = _instant(args.get("starts_at"))
    if starts_at is None:
        return Refusal(
            "starts_at geçerli bir zaman değil; find_slots'un verdiği değeri aynen kullan."
        )
    name = str(args.get("customer_name") or "").strip()
    if len(name) < 2 or normalise(name) in PLACEHOLDER_NAMES:
        return Refusal("Randevu için müşterinin adını sor.")
    if service.deposit_cents and not args.get("customer_email"):
        return Refusal("Bu hizmet kapora alıyor; önce müşterinin e-posta adresini iste.")
    # A new time from a customer who already has this service booked may be
    # a move: the model asks first, then the confirmation names the one that stays.
    same = [b for b in deps.bookings if b.service_id == service.id and b.status != "cancelled"]
    if same and not asked:
        return Refusal(
            SECOND_BOOKING.format(booking=f"{same[0].service_name} · {same[0].local_label}")
        )
    if service.deposit_cents:
        terms = (
            f"{format_money(service.deposit_cents, service.currency)} kapora "
            "(ödeme linki 30 dk geçerli)"
        )
    else:
        terms = format_money(service.price_cents, service.currency)
    when = local_label(starts_at, deps.business.timezone)
    second = (
        f" Bu ikinci bir randevu olur; duran randevunuz: {same[0].service_name} · "
        f"{same[0].local_label}. Onu taşımak isterseniz söyleyin."
        if same
        else ""
    )
    return Proposal(
        text=f"{name} adına: {service.name} · {when} · {service.duration_minutes} dk · {terms}."
        f"{second} {QUESTION}",
        args={**args, "customer_name": name, "expect_deposit_cents": service.deposit_cents},
    )


async def _own_booking(
    args: dict[str, Any], rezerve: RezerveSession, now: datetime
) -> BookingView | Refusal:
    try:
        booking = await rezerve.booking(str(args.get("booking_id") or ""))
    except RezerveError as error:
        return Refusal(error.message or "Randevu bulunamadı.")
    if booking.status == "cancelled":
        return Refusal("Bu randevu zaten iptal edilmiş.")
    if booking.starts_at <= now:
        return Refusal("Başlamış ya da geçmiş bir randevu değiştirilemez.")
    return booking


async def _propose_reschedule(
    args: dict[str, Any], deps: TurnDeps, rezerve: RezerveSession, now: datetime
) -> Proposal | Refusal:
    booking = await _own_booking(args, rezerve, now)
    if isinstance(booking, Refusal):
        return booking
    if booking.status == "pending":
        # The model, refused, went on to propose a cancellation without a word
        # about why: the customer hears it from code.
        return Refusal(
            "Kaporası henüz ödenmemiş bir randevu taşınamaz.",
            tell="Bu randevunun kaporası henüz ödenmediği için taşınamıyor.",
            unless="kapora",
        )
    notice = timedelta(hours=deps.business.refund_notice_hours)
    if booking.paid and booking.starts_at - now < notice:
        return Refusal(
            f"Kaporası ödenmiş bir randevu son {deps.business.refund_notice_hours} saatte "
            "taşınamaz; müşteriyi işletmeye yönlendir."
        )
    new_start = _instant(args.get("new_starts_at"))
    if new_start is None:
        return Refusal(
            "new_starts_at geçerli bir zaman değil; find_slots'un verdiği değeri aynen kullan."
        )
    moved = local_label(new_start, deps.business.timezone)
    return Proposal(
        text=f"{booking.service_name} randevunuz {booking.local_label} → {moved} olarak "
        f"değişecek. {QUESTION}",
        args=args,
    )


async def _propose_cancel(
    args: dict[str, Any], deps: TurnDeps, rezerve: RezerveSession, now: datetime
) -> Proposal | Refusal:
    booking = await _own_booking(args, rezerve, now)
    if isinstance(booking, Refusal):
        return booking
    hours = deps.business.refund_notice_hours
    paid = booking.paid and bool(booking.deposit_cents)
    refund = paid and booking.starts_at - now >= timedelta(hours=hours)
    if not paid:
        kapora = ""
    elif refund:
        kapora = " Kaporanız iade edilecek."
    else:
        kapora = f" {hours} saatten az kaldığı için kapora işletmede kalacak."
    return Proposal(
        text=f"{booking.service_name} · {booking.local_label} randevunuz iptal edilecek.{kapora} "
        f"{QUESTION}",
        args={**args, "expect_refund": refund},
    )


def resolution(approval: OpenApproval, decision: Resolution) -> DeferredToolResults:
    """The customer's answer, as the paused run takes it."""
    approvals: dict[str, bool | ToolApproved | ToolDenied] = {
        call_id: ToolDenied(ONE_AT_A_TIME) for call_id in approval.other_call_ids
    }
    if decision == "approve":
        approvals[approval.tool_call_id] = ToolApproved(override_args=approval.args)
    else:
        approvals[approval.tool_call_id] = ToolDenied(EXPIRED if decision == "expired" else DENIED)
    return DeferredToolResults(approvals=approvals)


def refusal(requests: DeferredToolRequests, reason: str) -> DeferredToolResults:
    """Send a call back to the model before the customer ever sees it."""
    first, *others = requests.approvals
    approvals: dict[str, bool | ToolApproved | ToolDenied] = {
        call.tool_call_id: ToolDenied(ONE_AT_A_TIME) for call in others
    }
    approvals[first.tool_call_id] = ToolDenied(reason)
    return DeferredToolResults(approvals=approvals)


def write_actions(writes: Sequence[CompletedWrite]) -> list[Action]:
    """A payment button for every kapora checkout this turn's writes opened."""
    actions: list[Action] = []
    for write in writes:
        url = (write.result or {}).get("checkout_url")
        if write.tool_name == "create_booking" and isinstance(url, str) and url:
            actions.append({"type": "link", "label": "Kaporayı öde", "url": url})
    return actions


def write_receipt(writes: Sequence[CompletedWrite]) -> str | None:
    """What this turn's writes did, in code's own words: for when the turn
    ends some other way than the model telling the customer itself."""
    lines = [_receipt_line(write) for write in writes]
    return " ".join(lines) if lines else None


def _receipt_line(write: CompletedWrite) -> str:
    if write.refusal is not None:
        return f"İşlem yapılamadı: {write.refusal.message}"
    result = write.result or {}
    when = result.get("local_label")
    if write.tool_name == "create_booking":
        if result.get("status") == "pending":
            return (
                f"Randevunuz ayrıldı: {when}. "
                "Kaporayı 30 dakika içinde ödediğinizde randevunuz kesinleşir."
            )
        return f"Randevunuz alındı: {when}."
    if write.tool_name == "reschedule_booking":
        return f"Randevunuz {when} olarak değiştirildi."
    if write.tool_name == "cancel_booking":
        if result.get("deposit_refunded"):
            return "Randevunuz iptal edildi. Kaporanız iade edildi."
        if result.get("refund_failed"):
            return "Randevunuz iptal edildi. Kapora iadesi başlatılamadı; işletme ilgilenecek."
        if result.get("deposit_kept"):
            return "Randevunuz iptal edildi. Kapora işletmede kaldı."
        return "Randevunuz iptal edildi."
    return "İşleminiz yapıldı."
