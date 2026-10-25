"""Rezerve, as this service reaches it: one MCP session per turn.

The session says who is asking — the business and the customer — in headers
the model never sees. The model gets the session's tools, every write wrapped
so it waits for the customer's yes; this service's own code reads through the
same session to build the instructions and the confirmations.
"""

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal, Self, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from pydantic_ai import (
    ApprovalRequiredToolset,
    PreparedToolset,
    RunContext,
    ToolDefinition,
    ToolFailed,
)
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

BUSINESS_HEADER = "X-Rezerve-Business"
CUSTOMER_HEADER = "X-Rezerve-Customer"


class BusinessInfo(BaseModel):
    name: str
    timezone: str
    today: str
    now_local: str
    phone: str | None
    address: str | None
    description: str | None
    assistant_notes: str | None
    booking_window_days: int
    min_lead_time_minutes: int
    refund_notice_hours: int
    booking_page_url: str | None


class ServiceInfo(BaseModel):
    id: str
    name: str
    description: str | None
    duration_minutes: int
    price_cents: int
    deposit_cents: int | None
    currency: str


class BookingView(BaseModel):
    id: str
    service_id: str
    service_name: str
    starts_at: datetime
    local_label: str
    status: Literal["pending", "confirmed", "cancelled"]
    deposit_cents: int | None
    paid: bool
    hold_expires_at: datetime | None
    cancelled_at: datetime | None
    cancelled_by: Literal["owner", "customer"] | None


class RezerveError(Exception):
    """A refusal from Rezerve, with its stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    @classmethod
    def from_failure(cls, text: str) -> "RezerveError":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "code" in payload:
            refusal = cast(dict[str, Any], payload)
            return cls(str(refusal["code"]), str(refusal.get("message", "")))
        # The MCP SDK words input validation failures itself, without a code.
        return cls("invalid", text)


def requires_approval(ctx: RunContext[Any], tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    """Every tool not declared read-only waits for the customer's yes — so a
    tool Rezerve adds without annotations is a write until it says otherwise."""
    annotations = cast(dict[str, Any], (tool_def.metadata or {}).get("annotations") or {})
    return annotations.get("readOnlyHint") is not True


def slim_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema without what only costs tokens: `$schema`, and the long
    regex zod emits beside a `format` that already says the same. Rezerve
    still validates every argument against the full schema."""
    slim: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "$schema" or (key == "pattern" and "format" in schema):
            continue
        slim[key] = _slim_value(value)
    return slim


def _slim_value(value: Any) -> Any:
    if isinstance(value, dict):
        return slim_schema(cast(dict[str, Any], value))
    if isinstance(value, list):
        return [_slim_value(item) for item in cast(list[Any], value)]
    return value


def _slim_tools(ctx: RunContext[Any], tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
    return [
        dataclasses.replace(tool, parameters_json_schema=slim_schema(tool.parameters_json_schema))
        for tool in tool_defs
    ]


@dataclass(frozen=True)
class CompletedWrite:
    """A write that ran on Rezerve this turn: its result, or Rezerve's refusal."""

    tool_name: str
    result: dict[str, Any] | None = None
    refusal: RezerveError | None = None


@dataclass
class _WriteRecorder(WrapperToolset[Any]):
    """Keeps what each write did, so its outcome reaches the customer even
    when no model is left to say it."""

    writes: list[CompletedWrite] = field(default_factory=list[CompletedWrite])

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        if not requires_approval(ctx, tool.tool_def, tool_args):
            return await super().call_tool(name, tool_args, ctx, tool)
        try:
            result: object = await super().call_tool(name, tool_args, ctx, tool)
        except ToolFailed as failure:
            self.writes.append(
                CompletedWrite(name, refusal=RezerveError.from_failure(failure.message))
            )
            raise
        self.writes.append(CompletedWrite(name, result=_as_object(result)))
        return result


# The most find_slots returns in one call.
MAX_SLOTS = 20


@dataclass
class _WholeDays(WrapperToolset[Any]):
    """One day's free times in full. Rezerve returns the earliest few and says
    the list was cut; searched without an earliest hour, a free late hour then
    looked taken. A search over one day is paged to its end; a search over
    several days keeps Rezerve's earliest times, which is what it is for."""

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        one_day = tool_args.get("from_date") == tool_args.get("to_date")
        business = getattr(ctx.deps, "business", None)
        if name != "find_slots" or not one_day or business is None:
            return await super().call_tool(name, tool_args, ctx, tool)
        zone = ZoneInfo(business.timezone)
        args = {**tool_args, "limit": MAX_SLOTS}
        found = _as_object(await super().call_tool(name, args, ctx, tool)) or {}
        slots: list[dict[str, Any]] = list(found.get("slots", []))
        truncated = bool(found.get("truncated"))
        for _ in range(10):
            if not (truncated and slots):
                break
            last = datetime.fromisoformat(slots[-1]["starts_at"]).astimezone(zone)
            after = last + timedelta(minutes=1)
            if after.date() != last.date():
                break
            more = await super().call_tool(
                name, {**args, "earliest_time": f"{after:%H:%M}"}, ctx, tool
            )
            page = _as_object(more) or {}
            slots += page.get("slots", [])
            truncated = bool(page.get("truncated"))
        return {**found, "slots": slots, "truncated": truncated}


def _as_object(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


class RezerveSession:
    """One business, one customer, one turn."""

    def __init__(self, mcp: MCPToolset[Any]) -> None:
        self._mcp = mcp
        self._recorder = _WriteRecorder(_WholeDays(mcp))
        self.toolset: AbstractToolset[Any] = PreparedToolset(
            ApprovalRequiredToolset(self._recorder, approval_required_func=requires_approval),
            _slim_tools,
        )

    @property
    def completed_writes(self) -> list[CompletedWrite]:
        """The writes that ran in this session, in order."""
        return self._recorder.writes

    async def __aenter__(self) -> Self:
        await self._mcp.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._mcp.__aexit__(exc_type, exc, traceback)

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = await self._mcp.direct_call_tool(tool, args or {})
        except ToolFailed as failure:
            raise RezerveError.from_failure(failure.message) from failure
        return cast(dict[str, Any], result)

    async def business_info(self) -> BusinessInfo:
        return BusinessInfo.model_validate(await self.call("get_business_info"))

    async def services(self) -> list[ServiceInfo]:
        result = await self.call("list_services")
        return [ServiceInfo.model_validate(service) for service in result["services"]]

    async def my_bookings(self) -> list[BookingView]:
        result = await self.call("list_my_bookings")
        return [BookingView.model_validate(booking) for booking in result["bookings"]]

    async def booking(self, booking_id: str) -> BookingView:
        return BookingView.model_validate(
            await self.call("get_booking", {"booking_id": booking_id})
        )


def connect(url: str, token: str, business_slug: str, customer_ref: str) -> RezerveSession:
    headers = {
        "Authorization": f"Bearer {token}",
        BUSINESS_HEADER: business_slug,
        CUSTOMER_HEADER: customer_ref,
    }
    return RezerveSession(MCPToolset(url, headers=headers, tool_error_behavior="failed"))
