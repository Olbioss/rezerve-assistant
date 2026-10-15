"""Rezerve in memory, in process: the contract snapshot's tools, with just
enough booking behaviour for the agent's tests. No network, no quota.

It serves `contract/rezerve-mcp-tools.json` verbatim, so the agent sees the
same tool list it gets from the real server; tests/contract checks that what
it returns fits that contract too.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, Tool
from pydantic_ai.mcp import MCPToolset

from assistant.agent.format import local_label
from assistant.mcp import RezerveSession

SNAPSHOT = Path(__file__).parents[2] / "contract" / "rezerve-mcp-tools.json"
TIMEZONE = "Europe/Istanbul"
CILT = "11111111-1111-4111-8111-111111111111"
SAC = "22222222-2222-4222-8222-222222222222"
CHECKOUT_URL = "https://sandbox-checkout.example/pay/1"

SERVICES: list[dict[str, Any]] = [
    {
        "id": CILT,
        "name": "Cilt Bakımı",
        "description": "Derinlemesine temizlik ve maske",
        "duration_minutes": 60,
        "price_cents": 150_000,
        "deposit_cents": 30_000,
        "currency": "try",
    },
    {
        "id": SAC,
        "name": "Saç Kesimi",
        "description": None,
        "duration_minutes": 45,
        "price_cents": 80_000,
        "deposit_cents": None,
        "currency": "try",
    },
]


def snapshot_tools() -> list[dict[str, Any]]:
    return json.loads(SNAPSHOT.read_text())["tools"]


class Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FakeBooking:
    id: str
    service_id: str
    starts_at: datetime
    customer_name: str
    status: str
    deposit_cents: int | None
    paid: bool
    cancelled_by: str | None = None


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FakeRezerve(MCPServer):
    def __init__(self) -> None:
        super().__init__(name="rezerve")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bookings: dict[str, FakeBooking] = {}
        self.phone: str | None = "0212 000 00 00"

    def session(self) -> RezerveSession:
        return RezerveSession(MCPToolset(self, tool_error_behavior="failed"))

    def add_booking(
        self,
        service_id: str,
        starts_at: datetime,
        *,
        status: str = "confirmed",
        paid: bool = False,
    ) -> FakeBooking:
        service = self._service(service_id)
        booking = FakeBooking(
            id=str(uuid.uuid4()),
            service_id=service_id,
            starts_at=starts_at,
            customer_name="Elif Kaya",
            status=status,
            deposit_cents=service["deposit_cents"],
            paid=paid,
        )
        self.bookings[booking.id] = booking
        return booking

    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        writes = {"create_booking", "reschedule_booking", "cancel_booking"}
        return [call for call in self.calls if call[0] in writes]

    async def list_tools(self) -> list[Tool]:
        return [Tool.model_validate(tool) for tool in snapshot_tools()]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Any = None
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        try:
            payload = getattr(self, f"_{name}")(arguments)
        except Refused as refusal:
            error = {"code": refusal.code, "message": refusal.message}
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(error, ensure_ascii=False))],
                structured_content=error,
                is_error=True,
            )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
        )

    def _service(self, service_id: str) -> dict[str, Any]:
        for service in SERVICES:
            if service["id"] == service_id:
                return service
        raise Refused("not_found", "Hizmet bulunamadı")

    def _view(self, booking: FakeBooking) -> dict[str, Any]:
        return {
            "id": booking.id,
            "service_id": booking.service_id,
            "service_name": self._service(booking.service_id)["name"],
            "starts_at": _iso(booking.starts_at),
            "local_label": local_label(booking.starts_at, TIMEZONE),
            "status": booking.status,
            "deposit_cents": booking.deposit_cents,
            "paid": booking.paid,
            "hold_expires_at": None,
            "cancelled_at": None,
            "cancelled_by": booking.cancelled_by,
        }

    def _get_business_info(self, args: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(ZoneInfo(TIMEZONE))
        return {
            "name": "Rezerve Demo Salon",
            "timezone": TIMEZONE,
            "today": now.date().isoformat(),
            "now_local": now.strftime("%Y-%m-%dT%H:%M"),
            "phone": self.phone,
            "address": "Örnek Sk. No:1, Kadıköy / İstanbul",
            "description": "Cilt bakımı, manikür ve saç.",
            "assistant_notes": None,
            "booking_window_days": 30,
            "min_lead_time_minutes": 60,
            "refund_notice_hours": 24,
            "booking_page_url": "http://localhost:3000/r/demo",
        }

    def _list_services(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"services": SERVICES}

    def _find_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        self._service(args["service_id"])
        day = datetime.fromisoformat(args["from_date"]).replace(tzinfo=ZoneInfo(TIMEZONE))
        starts = [day + timedelta(hours=hour) for hour in (10, 15.5, 16)]
        return {
            "slots": [
                {"starts_at": _iso(start), "local_label": local_label(start, TIMEZONE)}
                for start in starts
            ],
            "truncated": False,
        }

    def _list_my_bookings(self, args: dict[str, Any]) -> dict[str, Any]:
        upcoming = [b for b in self.bookings.values() if b.status != "cancelled"]
        return {"bookings": [self._view(b) for b in upcoming]}

    def _get_booking(self, args: dict[str, Any]) -> dict[str, Any]:
        booking = self.bookings.get(args["booking_id"])
        if booking is None:
            raise Refused("not_found", "Randevu bulunamadı.")
        return self._view(booking)

    def _create_booking(self, args: dict[str, Any]) -> dict[str, Any]:
        service = self._service(args["service_id"])
        deposit = service["deposit_cents"]
        if "expect_deposit_cents" in args and args["expect_deposit_cents"] != deposit:
            raise Refused("terms_changed", "Hizmetin kaporası değişmiş — lütfen yeniden onaylayın.")
        if deposit and not args.get("customer_email"):
            raise Refused("email_required", "Kaporalı randevu için e-posta gerekli.")
        starts_at = datetime.fromisoformat(args["starts_at"])
        booking = FakeBooking(
            id=str(uuid.uuid4()),
            service_id=service["id"],
            starts_at=starts_at,
            customer_name=args["customer_name"],
            status="pending" if deposit else "confirmed",
            deposit_cents=deposit,
            paid=False,
        )
        self.bookings[booking.id] = booking
        return {
            "booking_id": booking.id,
            "status": booking.status,
            "starts_at": _iso(starts_at),
            "local_label": local_label(starts_at, TIMEZONE),
            "checkout_url": CHECKOUT_URL if deposit else None,
            "hold_expires_at": _iso(datetime.now(UTC) + timedelta(minutes=30)) if deposit else None,
        }

    def _reschedule_booking(self, args: dict[str, Any]) -> dict[str, Any]:
        booking = self.bookings.get(args["booking_id"])
        if booking is None:
            raise Refused("not_found", "Randevu bulunamadı.")
        previous = booking.starts_at
        booking.starts_at = datetime.fromisoformat(args["new_starts_at"])
        return {
            "booking_id": booking.id,
            "starts_at": _iso(booking.starts_at),
            "local_label": local_label(booking.starts_at, TIMEZONE),
            "previous_local_label": local_label(previous, TIMEZONE),
        }

    def _cancel_booking(self, args: dict[str, Any]) -> dict[str, Any]:
        booking = self.bookings.get(args["booking_id"])
        if booking is None:
            raise Refused("not_found", "Randevu bulunamadı.")
        booking.status = "cancelled"
        booking.cancelled_by = "customer"
        refunded = booking.paid and bool(args.get("expect_refund"))
        return {
            "cancelled": True,
            "deposit_refunded": refunded,
            "deposit_kept": booking.paid and not refunded,
            "refund_failed": False,
        }
