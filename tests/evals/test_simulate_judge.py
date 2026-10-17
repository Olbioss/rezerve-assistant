from pathlib import Path

from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evals.cache import DiskCache
from evals.graders import Line
from evals.judge import Judge, Score
from evals.scenario import Expect, Scenario
from evals.simulate import Simulator

SCENARIO = Scenario(
    id="sim",
    tags=["booking"],
    mode="simulated",
    persona="Elif — kısa yazar",
    goal="Haftaya salı saç kesimi randevusu al.",
    facts={"ad": "Elif Kaya"},
    expect=Expect(),
)


def speaking(*texts: str) -> tuple[FunctionModel, list[str]]:
    """A model saying `texts` in turn, keeping the instructions it was given."""
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        seen.append(request.instructions or "")
        return ModelResponse(parts=[TextPart(texts[len(seen) - 1])])

    return FunctionModel(respond, model_name="groq-test"), seen


async def test_the_customer_plays_its_part_and_says_when_done(tmp_path: Path) -> None:
    model, seen = speaking("merhaba, haftaya salı saç kesimi olur mu?", "[[BITTI]]")
    simulator = Simulator(model, DiskCache(tmp_path))

    first = await simulator.next_message(SCENARIO, [])
    assert first == "merhaba, haftaya salı saç kesimi olur mu?"
    assert "Haftaya salı saç kesimi randevusu al." in seen[0]
    assert "- ad: Elif Kaya" in seen[0]

    lines = [Line("customer", first), Line("assistant", "Randevunuz alındı.")]
    assert await simulator.next_message(SCENARIO, lines) is None


async def test_the_same_conversation_is_answered_from_the_cache(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    model, seen = speaking("merhaba")
    await Simulator(model, cache).next_message(SCENARIO, [])
    # A new simulator on the same cache: the model is not asked again.
    again, seen_again = speaking("başka bir şey")
    assert await Simulator(again, cache).next_message(SCENARIO, []) == "merhaba"
    assert (len(seen), seen_again, cache.hits) == (1, [], 1)


async def test_a_damaged_cache_entry_is_computed_again(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)

    async def compute() -> str:
        return "fresh"

    assert await cache.get_or_compute("ns", ["key"], compute) == "fresh"
    [entry] = (tmp_path / "ns").glob("*.json")
    entry.write_text('"cut sho', encoding="utf-8")
    assert await cache.get_or_compute("ns", ["key"], compute) == "fresh"
    assert cache.misses == 2


async def test_the_judge_scores_against_the_rubric(tmp_path: Path) -> None:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        assert "clarity (açıklık)" in (request.instructions or "")
        args = {"fluency": 5, "politeness": 4, "clarity": 5, "note": "Doğal ve kısa."}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    judge = Judge(FunctionModel(respond, model_name="groq-test"), DiskCache(tmp_path))
    score = await judge.score([Line("customer", "merhaba"), Line("assistant", "Merhaba!")])
    assert score == Score(fluency=5, politeness=4, clarity=5, note="Doğal ve kısa.")
