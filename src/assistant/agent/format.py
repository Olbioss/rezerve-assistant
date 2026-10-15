"""Times and money written the way Rezerve writes them, so a confirmation
reads exactly like the booking page: "Cumartesi 17 Ekim 15:30", "₺1.500"."""

from datetime import datetime
from zoneinfo import ZoneInfo

# Indexed by date.weekday(): Monday is 0.
WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
MONTHS = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)
CURRENCY_SYMBOLS = {"try": "₺"}


def local_label(instant: datetime, timezone: str) -> str:
    local = instant.astimezone(ZoneInfo(timezone))
    return f"{WEEKDAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]} {local:%H:%M}"


def format_money(cents: int, currency: str) -> str:
    """Turkish grouping: ₺1.500, and ₺1.500,50 when there are kuruş to show."""
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency.upper()} ")
    lira, kurus = divmod(cents, 100)
    amount = f"{lira:,}".replace(",", ".")
    if kurus:
        amount += f",{kurus:02d}"
    return f"{symbol}{amount}"
