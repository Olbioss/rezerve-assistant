"""MCP is the boundary between the two repositories, and this is its guard.

`contract/rezerve-mcp-tools.json` is what Rezerve's `bun run mcp:schema`
prints: the tools exactly as /api/mcp serves them. These tests fail when the
snapshot and this service's expectations drift apart — the tools it relies
on, which of them write, the arguments it writes, the fields it reads — and
when the fake Rezerve the other tests use answers outside the contract.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from assistant.mcp import BookingView, BusinessInfo, ServiceInfo
from tests.fakes.rezerve import CILT, SAC, FakeBooking, FakeRezerve, snapshot_tools

TOOLS = {tool["name"]: tool for tool in snapshot_tools()}
WRITES = {"create_booking", "reschedule_booking", "cancel_booking"}
NOW = datetime.now(UTC)


def validate(schema: dict[str, Any], instance: object) -> None:
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        instance
    )


def properties(name: str, schema: str = "inputSchema") -> set[str]:
    return set(TOOLS[name][schema]["properties"])


def test_the_snapshot_has_the_tools_this_service_uses() -> None:
    assert set(TOOLS) == {
        "get_business_info",
        "list_services",
        "find_slots",
        "list_my_bookings",
        "get_booking",
        *WRITES,
    }


def test_exactly_the_writes_wait_for_approval() -> None:
    not_read_only = {
        name for name, tool in TOOLS.items() if tool["annotations"].get("readOnlyHint") is not True
    }
    assert not_read_only == WRITES


def test_no_tool_lets_the_model_name_a_business_or_a_customer() -> None:
    for name in TOOLS:
        for field in properties(name):
            assert not any(
                word in field for word in ("slug", "business", "organization", "customer_ref")
            ), f"{name}.{field}"


def test_the_arguments_this_service_writes_exist() -> None:
    assert {
        "service_id",
        "starts_at",
        "customer_name",
        "customer_email",
        "expect_deposit_cents",
    } <= (properties("create_booking"))
    assert {"booking_id", "new_starts_at"} <= properties("reschedule_booking")
    assert {"booking_id", "expect_refund"} <= properties("cancel_booking")
    # The instructions tell the model to pass it when moving a booking.
    assert "rescheduling_booking_id" in properties("find_slots")


@pytest.mark.parametrize(
    ("model", "schema"),
    [
        (BusinessInfo, TOOLS["get_business_info"]["outputSchema"]),
        (ServiceInfo, TOOLS["list_services"]["outputSchema"]["properties"]["services"]["items"]),
        (BookingView, TOOLS["get_booking"]["outputSchema"]),
    ],
)
def test_every_field_this_service_reads_is_one_rezerve_always_sends(
    model: type[BaseModel], schema: dict[str, Any]
) -> None:
    assert set(model.model_fields) <= set(schema["required"])


type Arguments = Callable[[FakeBooking], dict[str, Any]]

CALLS: list[tuple[str, Arguments]] = [
    ("get_business_info", lambda b: {}),
    ("list_services", lambda b: {}),
    (
        "find_slots",
        lambda b: {"service_id": SAC, "from_date": "2026-10-16", "to_date": "2026-10-16"},
    ),
    ("list_my_bookings", lambda b: {}),
    ("get_booking", lambda b: {"booking_id": b.id}),
    (
        "create_booking",
        lambda b: {
            "service_id": CILT,
            "starts_at": "2026-10-17T12:30:00Z",
            "customer_name": "Elif Kaya",
            "customer_email": "elif@example.com",
            "expect_deposit_cents": 30_000,
        },
    ),
    ("reschedule_booking", lambda b: {"booking_id": b.id, "new_starts_at": "2026-10-18T09:00:00Z"}),
    ("cancel_booking", lambda b: {"booking_id": b.id, "expect_refund": True}),
]


@pytest.mark.parametrize(("name", "arguments"), CALLS)
async def test_the_fake_answers_inside_the_contract(name: str, arguments: Arguments) -> None:
    fake = FakeRezerve()
    booking = fake.add_booking(CILT, NOW + timedelta(days=2), paid=True)
    args = arguments(booking)
    validate(TOOLS[name]["inputSchema"], args)
    result = await fake.call_tool(name, args)
    assert not result.is_error
    validate(TOOLS[name]["outputSchema"], result.structured_content)
