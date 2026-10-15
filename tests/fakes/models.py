"""Scripted models: each request gets the next response, and is kept for
the test to look at. Deterministic, offline, free."""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

type Step = (
    ModelResponse
    | Callable[[list[ModelMessage], AgentInfo], ModelResponse | Awaitable[ModelResponse]]
)


class Script:
    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.requests: list[ModelRequest] = []

    async def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        self.requests.append(request)
        assert self._steps, "the model was asked more often than the script allows"
        step = self._steps.pop(0)
        if not callable(step):
            return step
        response = step(messages, info)
        return await response if inspect.isawaitable(response) else response

    def add(self, *steps: Step) -> None:
        self._steps.extend(steps)

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self.respond, model_name="script")

    @property
    def done(self) -> bool:
        return not self._steps


def says(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def calls(tool: str, args: dict[str, Any], call_id: str = "c1") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args, tool_call_id=call_id)])


def classifies(decision: str) -> ModelResponse:
    """What a reply classifier's output tool call looks like."""
    return ModelResponse(parts=[ToolCallPart("final_result", {"response": decision})])
