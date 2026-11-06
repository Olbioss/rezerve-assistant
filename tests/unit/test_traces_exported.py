"""What the traces send to Langfuse, read from the real configuration
(spec §7.10): no secret, nothing a customer wrote, and the conversation as
the trace's session."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests import trace_probe

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def exported() -> list[dict[str, Any]]:
    """Every span tests.trace_probe exports, from a fresh process."""
    # Nothing in the environment may add an exporter or settings of its own.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OTEL_", "LOGFIRE_")) and key != "CONFIG_FILE"
    }
    run = subprocess.run(
        [sys.executable, "-m", "tests.trace_probe"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-3000:]
    spans: list[dict[str, Any]] = json.loads(run.stdout.splitlines()[-1])
    return spans


def test_a_request_trace_holds_no_secret_and_nothing_a_customer_wrote(
    exported: list[dict[str, Any]],
) -> None:
    # Logfire records each endpoint's arguments by default: the settings (the
    # database URL among them), the update a customer sent, the visitor id.
    assert any("/telegram/webhook" in span["name"] for span in exported)
    sent = json.dumps(exported)
    private = (trace_probe.DATABASE_PASSWORD, trace_probe.CUSTOMER_TEXT, trace_probe.VISITOR_ID)
    assert [value for value in private if value in sent] == []


def test_the_widgets_requests_leave_no_trace(exported: list[dict[str, Any]]) -> None:
    # The widget polls every second while a reply is coming, and every ten
    # seconds while the owner holds the conversation. As traces, its requests
    # would spend the 50k units a month Langfuse's free plan has for the turns.
    names = [span["name"] for span in exported]
    assert any("/telegram/webhook" in name for name in names)
    assert [name for name in names if "/web/" in name] == []


def test_the_conversation_reaches_langfuse_as_the_traces_session(
    exported: list[dict[str, Any]],
) -> None:
    # Logfire's scrubber redacts any key with "session" in it.
    sessions = {span["name"]: span["attributes"].get("langfuse.session.id") for span in exported}
    assert sessions["turn"] == trace_probe.CONVERSATION
    assert sessions["model call"] == trace_probe.CONVERSATION
