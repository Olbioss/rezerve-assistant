"""What the customer and the owner read when the assistant steps aside.

With an owner chat linked, a handoff hands the conversation to the owner on
Telegram (spec §7.6). Without one, the customer gets the business's own phone
number.
"""

from collections.abc import Sequence
from urllib.parse import urljoin

from assistant.mcp import BusinessInfo

HANDED_OFF = "Sizi işletmeye aktarıyorum, en kısa sürede dönüş yapılacak."
# When the owner hands the conversation back, or their 24 hours are over.
BACK = "Asistan yeniden sizinle. Size nasıl yardımcı olabilirim?"
HAND_BACK = "/bitir"
# A notice quotes this much of each message, and of the reason: six messages
# and how to answer fit in one Telegram message (4096 characters).
QUOTE = 400
REASON = 200


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


def owner_notice(business_slug: str, reason: str, recent: Sequence[tuple[str, str]]) -> str:
    """The owner's first word of a handoff: why, what was said, how to answer."""
    said = [
        f"{'Müşteri' if direction == 'in' else 'Asistan'}: {_cut(text, QUOTE)}"
        for direction, text in recent
    ]
    return "\n".join(
        [
            f"🔔 {business_slug}: bir müşteri size aktarıldı.",
            f"Neden: {_cut(reason, REASON)}",
            "",
            "Son mesajlar:",
            *said,
            "",
            "Yanıtlamak için bu mesajı yanıtlayın. "
            f"Sohbeti asistana geri vermek için {HAND_BACK} yazarak yanıtlayın.",
        ]
    )


def relayed(text: str) -> str:
    """What the customer wrote, as the owner reads it."""
    return f"Müşteri: {text}"


def from_owner(text: str) -> str:
    """What the owner wrote, as the customer reads it."""
    return f"İşletme: {text}"


def owner_unreachable(booking_page_url: str) -> str:
    """Telegram refused the owner's chat, so the assistant answers again (spec §7.5)."""
    return (
        "İşletmeye şu an ulaşamadım; dilerseniz size ben yardımcı olmaya devam edeyim. "
        f"İşletmenin iletişim bilgileri randevu sayfasında: {booking_page_url}"
    )


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def booking_page(rezerve_mcp_url: str, business_slug: str) -> str:
    """The booking page, worked out from the MCP address when Rezerve is unreachable."""
    return urljoin(rezerve_mcp_url, f"/r/{business_slug}")


def unavailable_reply(booking_page_url: str) -> str:
    """Spec §7.5: the customer always gets an answer."""
    return f"Şu an yanıt veremiyorum — randevunuzu buradan alabilirsiniz: {booking_page_url}"
