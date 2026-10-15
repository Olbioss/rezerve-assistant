"""What the customer reads when the assistant steps aside.

Phase 2 has no owner chat to relay to (that is Telegram, phase 4), so a
handoff gives the business's own phone number, as spec §7.6 says to do when
no owner chat is linked.
"""

from urllib.parse import urljoin

from assistant.mcp import BusinessInfo


def handoff_reply(business: BusinessInfo) -> str:
    if business.phone:
        return (
            "Bu konuda size en doğru yanıtı işletme verebilir: "
            f"{business.phone} numarasından ulaşabilirsiniz."
        )
    return (
        "Bu konuda size en doğru yanıtı işletme verebilir. "
        f"İletişim bilgileri randevu sayfasında: {business.booking_page_url}"
    )


def booking_page(rezerve_mcp_url: str, business_slug: str) -> str:
    """The booking page, worked out from the MCP address when Rezerve is unreachable."""
    return urljoin(rezerve_mcp_url, f"/r/{business_slug}")


def unavailable_reply(booking_page_url: str) -> str:
    """Spec §7.5: the customer always gets an answer."""
    return f"Şu an yanıt veremiyorum — randevunuzu buradan alabilirsiniz: {booking_page_url}"
