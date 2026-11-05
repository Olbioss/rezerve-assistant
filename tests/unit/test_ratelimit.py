from starlette.requests import Request

from assistant.ratelimit import SlidingWindow, client_ip


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_key_gets_its_limit_in_a_window_and_no_more() -> None:
    clock = Clock()
    window = SlidingWindow(limit=2, seconds=600, clock=clock)
    assert [window.allow("a"), window.allow("a"), window.allow("a")] == [True, True, False]
    assert window.allow("b")


def test_the_window_slides() -> None:
    clock = Clock()
    window = SlidingWindow(limit=1, seconds=600, clock=clock)
    assert window.allow("a")
    clock.now += 599
    assert not window.allow("a")
    clock.now += 2
    assert window.allow("a")


def test_keys_whose_window_has_passed_are_forgotten() -> None:
    clock = Clock()
    window = SlidingWindow(limit=1, seconds=600, clock=clock, max_keys=2)
    for key in "abc":
        window.allow(key)
        clock.now += 601
    assert len(window) <= 2


def request(forwarded_for: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    return Request({"type": "http", "headers": headers, "client": ("10.0.0.9", 51000)})


def test_the_client_is_counted_from_the_right_of_x_forwarded_for() -> None:
    # Entries on the left come from the client, who can write anything there;
    # the ones the platform appended are on the right.
    assert client_ip(request("6.6.6.6, 203.0.113.7"), hops=1) == "203.0.113.7"
    assert client_ip(request("6.6.6.6, 203.0.113.7, 35.191.0.1"), hops=2) == "203.0.113.7"


def test_without_the_header_the_connection_is_the_client() -> None:
    assert client_ip(request(None), hops=1) == "10.0.0.9"
    assert client_ip(request("203.0.113.7"), hops=2) == "10.0.0.9"
