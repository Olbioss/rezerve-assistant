"""`assistant config check <file> [--expect KEY=VALUE ...]`: the production
config, checked on the operator's machine before deploy/push-config.sh
uploads it. Only the file counts, never the environment or a .env beside it.
Problems are named by key and never show a value: the file holds every
secret the service has."""

import json
from pathlib import Path
from typing import cast

from pydantic import SecretStr, ValidationError

from assistant.config import Settings, from_file

# What deploy/config.example.json holds where a value is still to be filled in.
PLACEHOLDER = "REPLACE_ME"


def problems(path: Path, expected: dict[str, str]) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"Not a JSON object: {error.msg}, line {error.lineno}"]
    if not isinstance(raw, dict):
        return ["Not a JSON object"]
    values = cast(dict[str, object], raw)
    found = [f"{key}: not a setting" for key in values if not _is_setting(key)]
    found += [
        f"{key}: still the example's placeholder"
        for key, value in values.items()
        if isinstance(value, str) and PLACEHOLDER in value
    ]
    try:
        settings = from_file(path)
    except ValidationError as error:
        return found + [_line(detail["loc"], detail["msg"]) for detail in error.errors()]
    for key, value in expected.items():
        if not _is_setting(key):
            found.append(f"{key}: not a setting")
        elif _shown(getattr(settings, key.lower())) != value:
            found.append(f"{key}: should be {value}")
    return found


def _is_setting(key: str) -> bool:
    return key.lower() in Settings.model_fields


def _shown(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return "" if value is None else str(value)


def _line(location: tuple[int | str, ...], message: str) -> str:
    # Pydantic's messages for a validator's ValueError start "Value error, ".
    message = message.removeprefix("Value error, ")
    return f"{str(location[0]).upper()}: {message}" if location else message
