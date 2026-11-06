"""The service's log lines, as Cloud Logging reads them: Cloud Run sends what
a container writes to Cloud Logging, which turns a line holding one JSON
object into an entry with that object's severity."""

import json
import logging
import subprocess
import sys

from assistant import logs

# The container's own logging, in a fresh process: configuring it here would
# change the logging of every test after this one.
SERVED = """
import logging
from assistant import serve

serve.config()
logging.getLogger("assistant.reminders").info("No reminder for booking %s", "b1")
logging.getLogger("assistant.reminders").debug("Not this one")
logging.getLogger("uvicorn.error").info("Application startup complete.")
logging.getLogger("uvicorn.access").info("GET /health 200")
logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/bot123:SECRET/sendMessage")
"""


def test_a_line_is_one_json_object_with_its_severity() -> None:
    record = logging.LogRecord(
        "assistant.test", logging.WARNING, __file__, 1, "Chat %s unreachable", (42,), None
    )
    assert json.loads(logs.CloudFormatter().format(record)) == {
        "severity": "WARNING",
        "message": "Chat 42 unreachable",
        "logger": "assistant.test",
    }


def test_an_error_keeps_its_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "assistant.test", logging.ERROR, __file__, 1, "Turn failed", (), sys.exc_info()
        )
    entry = json.loads(logs.CloudFormatter().format(record))
    assert entry["severity"] == "ERROR"
    # Error Reporting groups errors by the stack trace in the message.
    assert entry["message"].startswith("Turn failed\nTraceback")
    assert "RuntimeError: boom" in entry["message"]


def test_the_service_logs_from_info_up_and_no_request_urls() -> None:
    # Unconfigured, Python drops the service's INFO lines and prints its
    # warnings bare, with no severity for Cloud Logging to read. Cloud Run
    # logs every request itself; httpx's request lines carry the bot token.
    done = subprocess.run(
        [sys.executable, "-c", SERVED], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stderr == ""
    entries = [json.loads(line) for line in done.stdout.splitlines()]
    assert entries == [
        {
            "severity": "INFO",
            "message": "No reminder for booking b1",
            "logger": "assistant.reminders",
        },
        {
            "severity": "INFO",
            "message": "Application startup complete.",
            "logger": "uvicorn.error",
        },
    ]
