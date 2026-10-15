from typing import Any, cast

from pydantic_ai import ToolDefinition

from assistant.mcp import RezerveError, requires_approval, slim_schema

NO_CONTEXT = cast(Any, None)


def tool(metadata: dict[str, Any] | None) -> ToolDefinition:
    return ToolDefinition(name="some_tool", metadata=metadata)


def test_a_read_only_tool_runs_without_asking() -> None:
    read = tool({"annotations": {"readOnlyHint": True}})
    assert requires_approval(NO_CONTEXT, read, {}) is False


def test_a_write_waits_for_approval() -> None:
    write = tool({"annotations": {"readOnlyHint": False, "destructiveHint": True}})
    assert requires_approval(NO_CONTEXT, write, {}) is True


def test_a_tool_without_annotations_is_treated_as_a_write() -> None:
    assert requires_approval(NO_CONTEXT, tool({"annotations": None}), {}) is True
    assert requires_approval(NO_CONTEXT, tool(None), {}) is True


def test_slimming_drops_the_regex_beside_a_format_and_the_schema_url() -> None:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "service_id": {"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]+$"},
            "earliest_time": {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"},
            "customer_email": {
                "anyOf": [
                    {"type": "string", "format": "email", "pattern": "^.+@.+$"},
                    {"type": "null"},
                ]
            },
        },
        "required": ["service_id"],
    }
    assert slim_schema(schema) == {
        "type": "object",
        "properties": {
            "service_id": {"type": "string", "format": "uuid"},
            "earliest_time": {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"},
            "customer_email": {"anyOf": [{"type": "string", "format": "email"}, {"type": "null"}]},
        },
        "required": ["service_id"],
    }


def test_a_refusal_keeps_rezerves_code() -> None:
    error = RezerveError.from_failure('{"code":"not_found","message":"Randevu bulunamadı."}')
    assert (error.code, error.message) == ("not_found", "Randevu bulunamadı.")


def test_an_uncoded_failure_is_invalid_input() -> None:
    text = "Input validation error: Invalid arguments for tool find_slots: service_id: Invalid UUID"
    assert RezerveError.from_failure(text).code == "invalid"
