from assistant.events import signature


def test_the_signature_rezerve_computes_too() -> None:
    # Rezerve's lib/assistant/events.test.ts pins the same value: a change on
    # one side fails both suites, not production.
    body = b'{"type":"booking.confirmed","booking_id":"b1"}'
    assert signature("event-secret", "1767225600", body) == (
        "sha256=c8ecbff0f5205233624b744225a4aa6d3f3c9a1dc7dda4377ab39c8ffe48de47"
    )
