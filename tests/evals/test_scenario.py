from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.scenario import Scenario, load_scenarios


def scenario(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "x",
        "tags": ["booking"],
        "mode": "scripted",
        "persona": "p",
        "turns": ["merhaba"],
        "expect": {},
    }
    return base | fields


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"turns": []}, "a scripted scenario needs turns"),
        ({"mode": "simulated", "turns": []}, "a simulated scenario needs a goal"),
        ({"expect": {"max_turns": 1}, "turns": ["a", "b"]}, "more turns than max_turns"),
        ({"expect": {"cancelled": ["nope"]}}, "'nope' is not a booking seeded for this customer"),
        (
            {
                "seed": [{"key": "a", "service": "Manikür", "day": "tomorrow", "at": "10:00"}],
                "meanwhile": [{"key": "a", "service": "Manikür", "day": "tomorrow", "at": "11:00"}],
            },
            "seed keys must be unique",
        ),
        (
            {"expect": {"bookings": [{"service": "Manikür", "day": "tomorrow"}]}},
            "give exactly one of at or between",
        ),
        # YAML 1.1 reads a bare `on:` key as `true`: the field is `day`.
        (
            {"expect": {"bookings": [{True: "tomorrow", "service": "Manikür", "at": "10:00"}]}},
            "Keys should be strings",
        ),
    ],
)
def test_a_malformed_scenario_is_refused(fields: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        Scenario.model_validate(scenario(**fields))


def test_a_file_must_be_named_after_its_scenario(tmp_path: Path) -> None:
    (tmp_path / "other-name.yaml").write_text(
        "id: x\ntags: [faq]\nmode: scripted\npersona: p\nturns: [merhaba]\nexpect: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must match the file name"):
        load_scenarios(tmp_path)
