import pytest
from pydantic_ai import Agent, ModelHTTPError, ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evals.models import PatientModel, QuotaExhausted, is_daily, retry_hint

GEMINI_PER_MINUTE = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota. Please retry in 7.2s.",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}],
    }
}
GEMINI_PER_DAY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota. Please retry in 14h39m40.59s.",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}],
    }
}
GROQ_TOKENS_PER_DAY = {
    "error": {
        "message": "Rate limit reached for model `openai/gpt-oss-120b` on tokens per day "
        "(TPD): Limit 200000, Used 199543, Requested 1234. Please try again in 7m12.5s.",
        "code": "rate_limit_exceeded",
    }
}
GROQ_PER_DAY = {
    "error": {
        "message": "Rate limit reached for model `openai/gpt-oss-120b` on requests per day "
        "(RPD): Limit 1000, Used 1000, Requested 1. Please try again in 1m26.4s.",
        "code": "rate_limit_exceeded",
    }
}


def limited(*errors: ModelHTTPError) -> tuple[FunctionModel, list[int]]:
    """A model that fails with `errors` in turn, then answers."""
    calls: list[int] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return ModelResponse(parts=[TextPart("tamam")])

    return FunctionModel(respond, model_name="gemini-test"), calls


def patient(model: FunctionModel) -> tuple[PatientModel, list[float]]:
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    return PatientModel(model, sleep=sleep), waited


def http(status: int, body: object, headers: dict[str, str] | None = None) -> ModelHTTPError:
    return ModelHTTPError(status, "gemini-test", body, headers=headers)


async def test_a_per_minute_limit_is_waited_out_as_long_as_asked() -> None:
    model, calls = limited(http(429, GEMINI_PER_MINUTE))
    wrapped, waited = patient(model)
    assert (await Agent(wrapped).run("merhaba")).output == "tamam"
    assert waited == [pytest.approx(8.2)]
    assert len(calls) == 2


async def test_without_a_hint_the_waits_grow() -> None:
    model, _ = limited(http(429, {"error": "slow down"}), http(429, {"error": "slow down"}))
    wrapped, waited = patient(model)
    await Agent(wrapped).run("merhaba")
    assert waited == [15.0, 30.0]
    assert wrapped.waited == 45.0


async def test_a_server_error_goes_straight_on_to_the_fallback() -> None:
    model, calls = limited(http(503, {"error": {"status": "UNAVAILABLE"}}))
    wrapped, waited = patient(model)
    with pytest.raises(ModelHTTPError):
        await Agent(wrapped).run("merhaba")
    assert (waited, len(calls)) == ([], 1)


@pytest.mark.parametrize("body", [GEMINI_PER_DAY, GROQ_PER_DAY, GROQ_TOKENS_PER_DAY])
async def test_a_daily_limit_stops_at_once_and_is_remembered(body: object) -> None:
    model, calls = limited(http(429, body))
    wrapped, waited = patient(model)
    with pytest.raises(QuotaExhausted, match="gemini-test has used up its daily free quota"):
        await Agent(wrapped).run("merhaba")
    assert (waited, len(calls)) == ([], 1)
    assert wrapped.exhausted is not None


async def test_other_client_errors_are_not_retried() -> None:
    model, calls = limited(http(400, {"error": "bad request"}))
    wrapped, waited = patient(model)
    with pytest.raises(ModelHTTPError):
        await Agent(wrapped).run("merhaba")
    assert (waited, len(calls), wrapped.exhausted) == ([], 1, None)


async def test_a_limit_that_never_lifts_is_raised_after_the_last_wait() -> None:
    model, calls = limited(*[http(429, {"error": "slow down"})] * 4)
    wrapped, waited = patient(model)
    with pytest.raises(ModelHTTPError):
        await Agent(wrapped).run("merhaba")
    assert (waited, len(calls)) == ([15.0, 30.0, 60.0], 4)


@pytest.mark.parametrize(
    ("error", "seconds", "daily"),
    [
        (http(429, GEMINI_PER_MINUTE), 7.2, False),
        (http(429, GEMINI_PER_DAY), 14 * 3600 + 39 * 60 + 40.59, True),
        (http(429, GROQ_PER_DAY), 86.4, True),
        # Groq's header beats the body.
        (http(429, GROQ_PER_DAY, {"Retry-After": "12"}), 12.0, True),
        (http(429, {"error": "slow down"}), None, False),
    ],
)
def test_what_a_limit_says_about_itself(
    error: ModelHTTPError, seconds: float | None, daily: bool
) -> None:
    assert retry_hint(error) == (pytest.approx(seconds) if seconds else None)
    assert is_daily(error) is daily
