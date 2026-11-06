"""Log lines Cloud Logging can read: Cloud Run sends what the container
writes to Cloud Logging, which makes a line holding one JSON object an entry
with that object's severity and message."""

import json
import logging
from typing import Any


class CloudFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            # Error Reporting groups errors by the stack trace in the message.
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return json.dumps({"severity": record.levelname, "message": message, "logger": record.name})


# What the service logs, from INFO up, on stdout: uvicorn's lines too.
CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"cloud": {"()": CloudFormatter}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "cloud",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"level": "INFO", "handlers": ["stdout"]},
    "loggers": {
        # httpx logs each request's URL at INFO, and Telegram's carry the bot token.
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
    },
}
