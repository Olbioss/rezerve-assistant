import httpx
import pytest

from assistant.channels.base import OutboundMessage
from assistant.channels.telegram import TelegramApi, TelegramChannel, TelegramError
from tests.fakes.telegram import FakeTelegram

TOKEN = "123456:secret-token"


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def api(telegram: FakeTelegram) -> TelegramApi:
    return TelegramApi(TOKEN, httpx.AsyncClient(transport=telegram.transport))


async def test_a_reply_is_sent_to_the_chat_and_its_id_kept(
    api: TelegramApi, telegram: FakeTelegram
) -> None:
    sent = await TelegramChannel(api).send("42", OutboundMessage(seq=7, text="Merhaba!"))
    assert sent == "telegram:101"
    assert telegram.sent() == [{"chat_id": 42, "text": "Merhaba!"}]


async def test_the_payment_link_is_a_button(api: TelegramApi, telegram: FakeTelegram) -> None:
    message = OutboundMessage(
        seq=8,
        text="Randevunuz ayrıldı.",
        actions=[{"type": "link", "label": "Kaporayı öde", "url": "https://pay.example/x"}],
    )
    await TelegramChannel(api).send("42", message)
    [payload] = telegram.sent()
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Kaporayı öde", "url": "https://pay.example/x"}]]
    }


@pytest.mark.parametrize(
    ("status", "description"),
    [(403, "Forbidden: bot was blocked by the user"), (400, "Bad Request: chat not found")],
)
async def test_a_chat_that_cannot_be_reached_is_given_up_on(
    api: TelegramApi, telegram: FakeTelegram, status: int, description: str
) -> None:
    # Retrying would never deliver it, and would hold up every reply after it.
    telegram.fail_with = (status, description)
    sent = await TelegramChannel(api).send("42", OutboundMessage(seq=7, text="Merhaba!"))
    assert sent == f"telegram:undelivered:{status}"


@pytest.mark.parametrize("status", [429, 502])
async def test_a_passing_failure_is_raised_so_the_turn_is_retried(
    api: TelegramApi, telegram: FakeTelegram, status: int
) -> None:
    telegram.fail_with = (status, "Too Many Requests: retry after 3")
    with pytest.raises(TelegramError):
        await TelegramChannel(api).send("42", OutboundMessage(seq=7, text="Merhaba!"))


async def test_a_text_too_long_for_telegram_is_cut_to_fit(
    api: TelegramApi, telegram: FakeTelegram
) -> None:
    # Telegram refuses a message over 4096 characters, and a refused one is lost.
    await api.send_message(42, "a" * 5000)
    [payload] = telegram.sent()
    assert len(payload["text"]) <= 4096
    assert payload["text"].startswith("a" * 3900)


async def test_the_bot_token_is_in_no_error_message(
    api: TelegramApi, telegram: FakeTelegram
) -> None:
    # The token is in Telegram's URLs; error messages end up in logs.
    telegram.fail_with = (502, "Bad Gateway")
    with pytest.raises(TelegramError) as raised:
        await api.send_message(42, "Merhaba!")
    assert "secret-token" not in str(raised.value)


async def test_a_network_failure_names_no_token_either() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    api = TelegramApi(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(TelegramError) as raised:
        await api.send_message(42, "Merhaba!")
    assert "secret-token" not in str(raised.value)
    assert raised.value.__suppress_context__
