"""The gcloud CLI, stood in for on PATH: each call is kept, a describe finds
only what exists, a create makes it exist, and a command can be given what
it prints."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

# The fake itself, run as `gcloud` in the scripts' own process.
PROGRAM = """
import json, os, sys

state_path = os.environ["FAKE_GCLOUD_STATE"]
with open(state_path) as file:
    state = json.load(file)
args = sys.argv[1:]
state["calls"].append(args)
words = " ".join(arg for arg in args if not arg.startswith("-"))
code = 0
if " create " in f" {words} " and "identity" not in words:
    state["existing"].append(words.replace(" create ", " describe ", 1))
answer = next((state["answers"][key] for key in state["answers"] if words.startswith(key)), None)
if " describe " in f" {words} " and words not in state["existing"]:
    print(f"ERROR: NOT_FOUND: {words}", file=sys.stderr)
    code = 1
elif answer is not None:
    print(answer)
with open(state_path, "w") as file:
    json.dump(state, file)
sys.exit(code)
"""


class FakeGcloud:
    def __init__(self, directory: Path) -> None:
        self.bin = directory / "bin"
        self.bin.mkdir()
        program = self.bin / "gcloud"
        program.write_text(f"#!{sys.executable}\n{PROGRAM}", encoding="utf-8")
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        self.state = directory / "gcloud.json"
        self._write({"calls": [], "existing": [], "answers": {}})

    def exists(self, *described: str) -> None:
        """Each, as its describe command's words: "projects describe p"."""
        self._update(lambda state: state["existing"].extend(described))

    def answers(self, words: str, output: str) -> None:
        """What a command starting with these words prints; a describe, only
        once what it describes exists."""
        self._update(lambda state: state["answers"].__setitem__(words, output))

    def calls(self) -> list[str]:
        return [" ".join(call) for call in self._read()["calls"]]

    def changes(self) -> list[str]:
        """The calls that change something: no describe, list or lookup."""
        reads = (" describe ", " list ")
        return [call for call in self.calls() if not any(read in f" {call} " for read in reads)]

    def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        environment = (
            os.environ
            | {
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "FAKE_GCLOUD_STATE": str(self.state),
            }
            | env
        )
        return subprocess.run(
            ["bash", script, *args], capture_output=True, text=True, env=environment, timeout=120
        )

    def _read(self) -> dict[str, Any]:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def _write(self, state: dict[str, Any]) -> None:
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def _update(self, change: Any) -> None:
        state = self._read()
        change(state)
        self._write(state)
