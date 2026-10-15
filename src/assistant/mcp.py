"""Rezerve, as this service reaches it: one MCP session per turn.

The session says who is asking — the business and the customer — in headers
the model never sees. The model gets the session's tools, every write wrapped
so it waits for the customer's yes; this service's own code reads through the
same session to build the instructions and the confirmations.
"""

import dataclasses
import json
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Self, cast

from pydantic import BaseModel
from pydantic_ai import (
    ApprovalRequiredToolset,
    PreparedToolset,
    RunContext,
    ToolDefinition,
    ToolFailed,
)
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

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


class RezerveSession:
    """One business, one customer, one turn."""

    def __init__(self, mcp: MCPToolset[Any]) -> None:
        self._mcp = mcp
        self.toolset: AbstractToolset[Any] = PreparedToolset(
            ApprovalRequiredToolset(mcp, approval_required_func=requires_approval), _slim_tools
        )

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
