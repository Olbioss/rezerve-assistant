from assistant.agent.handoff import HAND_BACK, owner_notice

# Telegram refuses a message longer than this, and a refused notice reaches no owner.
TELEGRAM_LIMIT = 4096


def test_a_long_conversation_still_fits_one_telegram_message() -> None:
    recent = [("in", "ç" * 1000), ("out", "ğ" * 2000)] * 3
    notice = owner_notice("demo", "şikayet " * 100, recent)
    assert len(notice) <= TELEGRAM_LIMIT
    # How to answer comes last, and survives.
    assert notice.endswith(f"{HAND_BACK} yazarak yanıtlayın.")
