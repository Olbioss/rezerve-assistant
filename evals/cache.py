"""A content-addressed cache for the simulated customer's and the judge's
answers (spec §8.5), ported from AskDocs' harness. The key hashes every
input, so an entry can never go stale: change the model, the instructions or
one word of the transcript, and it is a different key.

The agent is never cached. It is what the run measures.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).parent / ".cache"


class DiskCache:
    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self._directory = directory
        self.hits = 0
        self.misses = 0

    def _file(self, namespace: str, parts: object) -> Path:
        key = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self._directory / namespace / f"{digest}.json"

    async def get_or_compute(
        self, namespace: str, parts: object, compute: Callable[[], Awaitable[Any]]
    ) -> Any:
        """The JSON value cached for `parts`, computing and storing it on a miss."""
        file = self._file(namespace, parts)
        try:
            value = json.loads(file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            # Missing, or cut short by an interrupted run: compute it again.
            pass
        else:
            self.hits += 1
            return value
        value = await compute()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.misses += 1
        return value
