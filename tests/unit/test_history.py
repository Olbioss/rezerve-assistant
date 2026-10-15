from pydantic_ai import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from assistant.conversation.history import close_open_calls, dump, load, trim


def ask(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(text)])


def answer(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def call(n: int) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("find_slots", {}, tool_call_id=f"c{n}")])


def returned(n: int) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart("find_slots", {"slots": []}, tool_call_id=f"c{n}")])


def exchanges(count: int) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for n in range(count):
        messages += [ask(f"soru {n}"), call(n), returned(n), answer(f"cevap {n}")]
    return messages


def is_clean_start(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and all(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def test_a_short_history_is_kept_whole() -> None:
    messages = exchanges(3)
    assert trim(messages, limit=20) == messages


def test_trimming_starts_at_the_next_customer_message() -> None:
    messages = exchanges(10)  # 40 messages, 4 per exchange
    trimmed = trim(messages, limit=18)  # the window opens mid-exchange
    assert trimmed == messages[24:]
    assert trimmed[0] is messages[24]  # "soru 6"


def test_trimming_never_starts_on_a_tool_return() -> None:
    messages = exchanges(10)
    for limit in range(1, len(messages)):
        assert is_clean_start(trim(messages, limit=limit)[0])


def test_a_long_run_of_tool_calls_is_kept_rather_than_cut() -> None:
    calls: list[ModelMessage] = [m for n in range(15) for m in (call(n), returned(n))]
    messages = [ask("soru"), *calls, answer("cevap")]
    assert trim(messages, limit=10) == messages


def test_a_resumed_request_is_not_a_place_to_start() -> None:
    resumed = ModelRequest(
        parts=[ToolReturnPart("create_booking", {}, tool_call_id="c9"), UserPromptPart("evet")]
    )
    start = ask("randevu")
    messages = [*exchanges(5), start, call(9), resumed, answer("tamam")]
    assert trim(messages, limit=3)[0] is start


def test_an_unanswered_proposal_is_dropped() -> None:
    question = ask("randevu")
    assert close_open_calls([question, call(1)]) == [question]


def test_a_finished_exchange_is_left_alone() -> None:
    messages = [ask("randevu"), answer("tabii")]
    assert close_open_calls(messages) == messages


def test_history_round_trips_through_json() -> None:
    messages = exchanges(2)
    assert dump(load(dump(messages))) == dump(messages)
