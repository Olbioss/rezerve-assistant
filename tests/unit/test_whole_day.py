from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from assistant.agent.agent import TurnDeps, build_agent
from assistant.agent.format import local_label
from tests.fakes.rezerve import SAC, TIMEZONE, FakeRezerve, _iso


class PagedRezerve(FakeRezerve):
    """find_slots as Rezerve answers it: a day's times from 09:00 to 18:45,
    every quarter hour, filtered by earliest and latest time, at most `limit`
    (10 unless asked), and truncated when there were more."""

    def __init__(self) -> None:
        super().__init__()
        self.slot_calls: list[dict[str, Any]] = []

    def _find_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        self._service(args["service_id"])
        self.slot_calls.append(args)
        day = datetime.fromisoformat(args["from_date"]).replace(tzinfo=ZoneInfo(TIMEZONE))
        starts = [day + timedelta(minutes=minute) for minute in range(9 * 60, 19 * 60, 15)]
        if earliest := args.get("earliest_time"):
            starts = [start for start in starts if f"{start:%H:%M}" >= earliest]
        if latest := args.get("latest_time"):
            starts = [start for start in starts if f"{start:%H:%M}" <= latest]
        limit = args.get("limit", 10)
        return {
            "slots": [
                {"starts_at": _iso(start), "local_label": local_label(start, TIMEZONE)}
                for start in starts[:limit]
            ],
            "truncated": len(starts) > limit,
        }


async def found_by_the_model(rezerve: PagedRezerve, args: dict[str, Any]) -> dict[str, Any]:
    """What the model reads back after calling find_slots with `args`."""
    returned: list[ToolReturnPart] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        returns = [part for part in request.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("find_slots", args)])
        returned.extend(returns)
        return ModelResponse(parts=[TextPart("Tamam.")])

    async with rezerve.session() as session:
        deps = TurnDeps(
            business=await session.business_info(), services=await session.services(), bookings=[]
        )
        await build_agent(FunctionModel(model)).run(
            "cuma 16:00 olur mu?", deps=deps, toolsets=[session.toolset]
        )
    [found] = returned
    assert isinstance(found.content, dict)
    return found.content


async def test_one_days_times_come_back_whole() -> None:
    # Searched for one day without an earliest hour, Rezerve's first 10 times
    # stopped at 15:30, and a free 16:00 was called taken. A day's list now
    # comes back whole, however Rezerve pages it.
    rezerve = PagedRezerve()
    day = {"service_id": SAC, "from_date": "2026-10-30", "to_date": "2026-10-30"}
    found = await found_by_the_model(rezerve, day)

    assert found["truncated"] is False
    assert len(found["slots"]) == 40
    assert found["slots"][-1]["local_label"].endswith("18:45")
    assert [call.get("earliest_time") for call in rezerve.slot_calls] == [None, "13:46"]


async def test_a_search_over_several_days_is_left_as_rezerve_answers_it() -> None:
    # The earliest times of a week are what "something next week" needs.
    rezerve = PagedRezerve()
    week = {"service_id": SAC, "from_date": "2026-10-26", "to_date": "2026-10-30"}
    found = await found_by_the_model(rezerve, week)

    assert (found["truncated"], len(found["slots"]), len(rezerve.slot_calls)) == (True, 10, 1)
