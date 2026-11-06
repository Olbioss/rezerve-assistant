"""`assistant config check`: the production config, checked on the
operator's machine before deploy/push-config.sh uploads it."""

import json
from pathlib import Path
from typing import Any

import pytest

from assistant.cli import main

EXAMPLE = Path(__file__).parents[2] / "deploy" / "config.example.json"


def example() -> dict[str, str]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def filled() -> dict[str, str]:
    """The example, each placeholder filled in."""
    return {key: value.replace("REPLACE_ME", "x1") for key, value in example().items()}


def check(tmp_path: Path, values: dict[str, Any], *expected: str) -> int:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return main(["config", "check", str(path), *[f"--expect={item}" for item in expected]])


def test_the_example_filled_in_has_no_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(tmp_path, filled()) == 0
    assert capsys.readouterr().out == "No problems.\n"


def test_each_placeholder_left_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert check(tmp_path, example()) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{key}: still the example's placeholder"
        for key, value in example().items()
        if "REPLACE_ME" in value
    ]


def test_a_problem_is_named_by_its_key_and_never_shows_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The file holds every secret the service has: a terminal's scrollback
    # is no place for them.
    neon = "postgresql://neondb_owner:hunter2@ep-x1.eu-central-1.aws.neon.tech/neondb"
    values = filled() | {
        "DATABASE_URL": f"{neon}?sslmode=require&channel_binding=require",
        "DAILY_REQUEST_CAP": "lots-of-them",
        "GEMINI_APIKEY": "AIza-misplaced",
    }
    assert check(tmp_path, values) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "GEMINI_APIKEY: not a setting",
        "DATABASE_URL: must start with postgresql+asyncpg://",
        "DAILY_REQUEST_CAP: Input should be a valid integer, unable to parse string as an integer",
    ]
    for value in ("hunter2", "lots-of-them", "AIza"):
        assert value not in out


def test_the_values_the_deploy_set_up_are_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A PUBLIC_BASE_URL that is not the service's own fails every Cloud Tasks
    # call back: its token is minted for the wrong audience.
    url = "https://assistant-123456789012.europe-west1.run.app"
    values = filled() | {"PUBLIC_BASE_URL": url, "GCP_PROJECT": "rezerve-assistant"}
    deployed = [
        f"PUBLIC_BASE_URL={url}",
        "GCP_PROJECT=rezerve-assistant",
        "GCP_REGION=europe-west1",
        "QUEUE_BACKEND=cloud_tasks",
    ]
    assert check(tmp_path, values, *deployed) == 0
    assert check(tmp_path, values, "GCP_PROJECT=rezerve-assistant-2", "NO_SUCH=1") == 1
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "GCP_PROJECT: should be rezerve-assistant-2",
        "NO_SUCH: not a setting",
    ]


def test_only_the_file_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The operator runs the check beside their own .env and shell variables:
    # neither may fill a gap the file has.
    monkeypatch.setenv("CUSTOMER_REF_SECRET", "from-the-shell")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CUSTOMER_REF_SECRET=from-dotenv\n", encoding="utf-8")
    values = filled()
    del values["CUSTOMER_REF_SECRET"]
    assert check(tmp_path, values) == 1
    assert capsys.readouterr().out == "CUSTOMER_REF_SECRET: Field required\n"


def test_a_file_that_is_not_json_is_named_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"ASSISTANT_MCP_TOKEN": "secret-token",}', encoding="utf-8")
    assert main(["config", "check", str(path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Not a JSON object: ")
    assert "secret-token" not in out
