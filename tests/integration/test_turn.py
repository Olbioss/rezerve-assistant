import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai import (
    ModelHTTPError,
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel

from assistant.agent.agent import build_agent
from assistant.agent.approvals import EXPIRED, ONE_AT_A_TIME, ReplyClassifier
from assistant.channels.base import ChannelName, InboundMessage, OutboundMessage
from assistant.conversation.lease import Lease, LeaseLost
from assistant.conversation.store import Reply, Store, TurnResult
from assistant.conversation.turn import TurnProcessor, TurnSettings
from assistant.identity import customer_ref
from assistant.mcp import RezerveSession
from tests.fakes.models import Script, calls, classifies, says
from tests.fakes.rezerve import CHECKOUT_URL, CILT, SAC, FakeRezerve

REF = customer_ref("secret", "web", "visitor-1")
TOMORROW_1530 = (datetime.now(UTC) + timedelta(days=1)).replace(
    hour=12, minute=30, second=0, microsecond=0
)
BOOK_CILT = {
    "service_id": CILT,
    "starts_at": TOMORROW_1530.isoformat().replace("+00:00", "Z"),
    "customer_name": "Elif Kaya",
    "customer_email": "elif@example.com",
}


class Outbox:
    name: ChannelName = "web"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send(self, channel_user_id: str, message: OutboundMessage) -> str:
        self.sent.append(message)
        return f"web:{message.seq}"

    @property
    def texts(self) -> list[str]:
        return [message.text for message in self.sent]


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


class Harness:
    def __init__(self, store: Store, script: Script, *, classifier: Script | None = None) -> None:
        self.store = store
        self.fake = FakeRezerve()
        self.outbox = Outbox()
        self.clock = Clock()
        self.open_rezerve: Callable[[str, str], RezerveSession] = lambda slug, ref: (
            self.fake.session()
        )
        self.settings = TurnSettings(
            request_limit=8,
            token_limit=40_000,
            approval_ttl=timedelta(minutes=10),
            lease_ttl=timedelta(minutes=1),
            lease_renew=timedelta(seconds=30),
            rezerve_mcp_url="http://localhost:3000/api/mcp",
        )
        self.script = script
        # A model of the test's own, such as a fallback chain; the script otherwise.
        self.model: Model | None = None
        self.classifier = classifier or Script()
        self.conversation_id = 0

    def processor(self) -> TurnProcessor:
        return TurnProcessor(
            store=self.store,
            agent=build_agent(self.model or self.script.model),
            classifier=ReplyClassifier(self.classifier.model),
            open_rezerve=lambda slug, ref: self.open_rezerve(slug, ref),
            channels={"web": self.outbox},
            settings=self.settings,
            clock=self.clock,
        )

    async def receive(self, *texts: str) -> None:
        for text in texts:
            message = InboundMessage("demo", "web", "visitor-1", str(uuid4()), text)
            recorded = await self.store.record_inbound(message, REF, self.clock())
            self.conversation_id = recorded.conversation_id

    async def say(self, *texts: str) -> list[str]:
        """The customer writes; returns what they read back."""
        before = len(self.outbox.sent)
        await self.receive(*texts)
        await self.processor().process(self.conversation_id)
        return self.outbox.texts[before:]


def prompt_of(script: Script, request: int = -1) -> list[Any]:
    return [
        part.content for part in script.requests[request].parts if isinstance(part, UserPromptPart)
    ]


def returns_of(script: Script, request: int = -1) -> list[ToolReturnPart]:
    return [part for part in script.requests[request].parts if isinstance(part, ToolReturnPart)]


async def test_a_burst_is_answered_once_in_order(store: Store) -> None:
    turn = Harness(store, Script(says("Merhaba! Yarın için hangi hizmete bakalım?")))
    replies = await turn.say("merhaba", "yarın yer var mı?")
    assert replies == ["Merhaba! Yarın için hangi hizmete bakalım?"]
    assert prompt_of(turn.script) == ["merhaba\nyarın yer var mı?"]
    assert await store.unprocessed_inbound(turn.conversation_id) == []


async def test_a_booking_is_confirmed_by_code_and_made_on_yes(store: Store) -> None:
    turn = Harness(
        store,
        Script(calls("create_booking", BOOK_CILT), says("Randevunuz ayrıldı.")),
    )
    [confirmation] = await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    assert confirmation.startswith("Elif Kaya adına: Cilt Bakımı · ")
    assert (
        "₺300 kapora (ödeme linki 30 dk geçerli). Onaylıyor musunuz? (evet / hayır)" in confirmation
    )
    assert turn.fake.writes() == []

    [reply] = await turn.say("evet")
    assert reply == "Randevunuz ayrıldı."
    assert turn.fake.writes() == [("create_booking", BOOK_CILT | {"expect_deposit_cents": 30_000})]
    assert turn.outbox.sent[-1].actions == [
        {"type": "link", "label": "Kaporayı öde", "url": CHECKOUT_URL}
    ]
    assert await store.open_approval(turn.conversation_id) is None
    assert prompt_of(turn.script) == ["evet"]


async def test_no_writes_nothing(store: Store) -> None:
    turn = Harness(store, Script(calls("create_booking", BOOK_CILT), says("Peki, iptal ettim.")))
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    assert await turn.say("hayır") == ["Peki, iptal ettim."]
    assert turn.fake.writes() == []
    [denied] = returns_of(turn.script)
    assert denied.outcome == "denied"


async def test_a_counter_proposal_goes_back_to_the_model(store: Store) -> None:
    sixteen = BOOK_CILT | {"starts_at": TOMORROW_1530.replace(hour=13, minute=0).isoformat()}
    turn = Harness(
        store,
        Script(calls("create_booking", BOOK_CILT), calls("create_booking", sixteen, "c2")),
        classifier=Script(classifies("other")),
    )
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    [confirmation] = await turn.say("15:30 değil 16:00 olsun")
    assert " 16:00 · " in confirmation
    assert prompt_of(turn.script) == ["15:30 değil 16:00 olsun"]
    approval = await store.open_approval(turn.conversation_id)
    assert approval is not None and approval.tool_call_id == "c2"
    assert turn.fake.writes() == []


async def test_an_expired_approval_never_runs(store: Store) -> None:
    turn = Harness(
        store, Script(calls("create_booking", BOOK_CILT), says("Süre doldu, yeniden bakalım."))
    )
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    turn.clock.now += timedelta(minutes=11)
    assert await turn.say("evet") == ["Süre doldu, yeniden bakalım."]
    assert turn.fake.writes() == []
    [expired] = returns_of(turn.script)
    assert expired.content == EXPIRED


async def test_what_code_cannot_confirm_goes_back_to_the_model_first(store: Store) -> None:
    no_email = {k: v for k, v in BOOK_CILT.items() if k != "customer_email"}
    turn = Harness(
        store,
        Script(calls("create_booking", no_email), says("Kapora için e-posta adresiniz nedir?")),
    )
    assert await turn.say("yarın 15:30 cilt bakımı, adım Elif Kaya") == [
        "Kapora için e-posta adresiniz nedir?"
    ]
    [refused] = returns_of(turn.script)
    assert refused.outcome == "denied"
    assert "e-posta" in str(refused.content)
    assert await store.open_approval(turn.conversation_id) is None


async def test_two_writes_at_once_are_confirmed_one_at_a_time(store: Store) -> None:
    booking = None
    two = ModelResponse(
        parts=[
            ToolCallPart("create_booking", BOOK_CILT, tool_call_id="c1"),
            ToolCallPart("cancel_booking", {"booking_id": "x"}, tool_call_id="c2"),
        ]
    )
    turn = Harness(store, Script(two, says("Cilt bakımınız ayrıldı.")))
    booking = turn.fake.add_booking(SAC, datetime.now(UTC) + timedelta(days=3))
    await turn.say("cilt bakımı al, saç kesimini iptal et")
    await turn.say("evet")
    assert [name for name, _ in turn.fake.writes()] == ["create_booking"]
    assert booking.status == "confirmed"
    outcomes = {part.tool_call_id: (part.outcome, part.content) for part in returns_of(turn.script)}
    assert outcomes["c2"] == ("denied", ONE_AT_A_TIME)


async def test_when_every_model_fails_the_customer_gets_the_booking_page(store: Store) -> None:
    def quota(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=429, model_name="gemini", body={"error": "quota"})

    turn = Harness(store, Script(quota))
    assert await turn.say("merhaba") == [
        "Şu an yanıt veremiyorum — randevunuzu buradan alabilirsiniz: http://localhost:3000/r/demo"
    ]
    assert await store.unprocessed_inbound(turn.conversation_id) == []


async def test_rezerve_unreachable_still_gets_an_answer(store: Store) -> None:
    turn = Harness(store, Script())

    def unreachable(slug: str, ref: str) -> RezerveSession:
        raise ConnectionError("Rezerve is down")

    turn.open_rezerve = unreachable
    [reply] = await turn.say("merhaba")
    assert reply.endswith("http://localhost:3000/r/demo")


async def test_a_handoff_gives_the_business_phone(store: Store) -> None:
    turn = Harness(store, Script(calls("handoff_to_owner", {"reason": "şikayet"})))
    assert await turn.say("berbat bir hizmetti, şikayetçiyim") == [
        "Bu konuda size en doğru yanıtı işletme verebilir: "
        "0212 000 00 00 numarasından ulaşabilirsiniz."
    ]


async def test_hitting_the_request_limit_hands_off(store: Store) -> None:
    looping = [
        calls(
            "find_slots",
            {"service_id": SAC, "from_date": "2026-10-16", "to_date": "2026-10-16"},
            f"c{n}",
        )
        for n in range(3)
    ]
    turn = Harness(store, Script(*looping))
    turn.settings = dataclasses.replace(turn.settings, request_limit=2)
    [reply] = await turn.say("yarın saç kesimi")
    assert "0212 000 00 00" in reply


async def test_a_crash_between_send_and_mark_resends_only_the_unsent(store: Store) -> None:
    turn = Harness(store, Script())
    await turn.receive("merhaba")
    [inbound] = await store.unprocessed_inbound(turn.conversation_id)
    result = TurnResult(processed=[inbound.seq], replies=[Reply("bir"), Reply("iki")])
    await store.commit_turn(turn.conversation_id, result, turn.clock())
    first, _ = await store.unsent(turn.conversation_id)
    await store.mark_sent(first.seq, "web:sent-before-the-crash", turn.clock())

    await turn.processor().process(turn.conversation_id)
    assert turn.outbox.texts == ["iki"]


async def test_a_message_that_lands_mid_turn_is_answered_by_the_same_handler(
    store: Store,
) -> None:
    turn = Harness(store, Script())

    async def meanwhile(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await turn.receive("bir de manikür var mı?")
        return says("Merhaba!")

    turn.script.add(meanwhile, says("Manikür için de bakalım."))
    assert await turn.say("merhaba") == ["Merhaba!", "Manikür için de bakalım."]
    assert prompt_of(turn.script) == ["bir de manikür var mı?"]


async def test_an_empty_answer_is_never_sent(store: Store) -> None:
    turn = Harness(store, Script(says("  ")))
    [reply] = await turn.say("merhaba")
    assert reply.endswith("http://localhost:3000/r/demo")


async def test_a_failing_classifier_leaves_the_approval_to_the_next_message(
    store: Store,
) -> None:
    def down(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=503, model_name="gemini", body=None)

    turn = Harness(
        store,
        Script(calls("create_booking", BOOK_CILT), says("Randevunuz ayrıldı.")),
        classifier=Script(down),
    )
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    [reply] = await turn.say("olur tabii, hemen")
    assert reply.startswith("Şu an yanıt veremiyorum")
    assert await store.open_approval(turn.conversation_id) is not None
    assert await turn.say("evet") == ["Randevunuz ayrıldı."]
    assert [name for name, _ in turn.fake.writes()] == ["create_booking"]


async def test_a_lapsed_lease_never_runs_an_approved_write(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn = Harness(store, Script(calls("create_booking", BOOK_CILT), says("Randevunuz ayrıldı.")))
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")

    def lapsed(self: Lease) -> None:
        raise LeaseLost(0)

    monkeypatch.setattr(Lease, "check", lapsed)
    with pytest.raises(LeaseLost):
        await turn.say("evet")
    assert turn.fake.writes() == []
    assert await store.open_approval(turn.conversation_id) is not None


async def test_a_failed_resume_after_a_mixed_proposal_does_not_wedge_the_conversation(
    store: Store,
) -> None:
    def down(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=503, model_name="gemini", body=None)

    slots = {"service_id": CILT, "from_date": "2026-10-16", "to_date": "2026-10-16"}
    read_and_write = ModelResponse(
        parts=[
            ToolCallPart("find_slots", slots, tool_call_id="r1"),
            ToolCallPart("create_booking", BOOK_CILT, tool_call_id="w1"),
        ]
    )
    turn = Harness(store, Script(read_and_write, down, says("Rica ederim!")))
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    await turn.say("evet")  # the write runs, then every model fails
    assert await turn.say("teşekkürler") == ["Rica ederim!"]


async def test_a_fallback_is_logged_with_the_model_that_failed(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    def quota(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=429, model_name="gemini-x", body={"error": "quota"})

    turn = Harness(store, Script(says("Merhaba!")))
    turn.model = FallbackModel(FunctionModel(quota, model_name="gemini-x"), turn.script.model)
    with caplog.at_level(logging.WARNING, logger="assistant.conversation.turn"):
        assert await turn.say("merhaba") == ["Merhaba!"]
    assert "gemini-x" in caplog.text
    assert "fell back" in caplog.text


def _down(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise ModelHTTPError(status_code=503, model_name="gemini", body=None)


KAPORA_BUTTON = [{"type": "link", "label": "Kaporayı öde", "url": CHECKOUT_URL}]


async def test_an_approved_write_is_reported_even_when_every_model_fails_after_it(
    store: Store,
) -> None:
    turn = Harness(store, Script(calls("create_booking", BOOK_CILT), _down))
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    receipt, unavailable = await turn.say("evet")
    assert receipt.startswith("Randevunuz ayrıldı: ")
    assert receipt.endswith("Kaporayı 30 dakika içinde ödediğinizde randevunuz kesinleşir.")
    assert turn.outbox.sent[-2].actions == KAPORA_BUTTON
    assert unavailable.startswith("Şu an yanıt veremiyorum")


async def test_a_handoff_right_after_a_write_still_carries_the_payment_button(
    store: Store,
) -> None:
    turn = Harness(
        store,
        Script(
            calls("create_booking", BOOK_CILT),
            calls("handoff_to_owner", {"reason": "müşteri fatura istiyor"}, "h1"),
        ),
    )
    await turn.say("yarın 15:30 cilt bakımı, Elif Kaya, elif@example.com")
    receipt, handoff = await turn.say("evet")
    assert receipt.startswith("Randevunuz ayrıldı: ")
    assert turn.outbox.sent[-2].actions == KAPORA_BUTTON
    assert "0212 000 00 00" in handoff
