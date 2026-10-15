import re

from assistant.identity import customer_ref

# Rezerve's check (lib/assistant/identity.ts), verbatim.
REZERVE_REF = re.compile(r"^(web|telegram|whatsapp|eval):[a-z2-7]{26}$")


def test_has_the_shape_rezerve_accepts() -> None:
    assert REZERVE_REF.match(customer_ref("secret", "telegram", "123456789"))


def test_is_hmac_sha256_in_lowercase_base32() -> None:
    # Cross-checked against Node's crypto.createHmac with a hand-rolled base32.
    ref = customer_ref("test-secret", "web", "3f2504e0-4f89-41d3-9a0c-0305e82c3301")
    assert ref == "web:go3kbqc7inuesaqj637cvgzksm"


def test_is_stable_and_differs_by_channel_user_and_secret() -> None:
    ref = customer_ref("secret", "web", "visitor-1")
    assert customer_ref("secret", "web", "visitor-1") == ref
    others = {
        customer_ref("secret", "telegram", "visitor-1"),
        customer_ref("secret", "web", "visitor-2"),
        customer_ref("other-secret", "web", "visitor-1"),
    }
    assert ref not in others
    assert len(others) == 3
