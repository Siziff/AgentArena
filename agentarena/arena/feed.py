"""The Arena feed store.

Aggregates commentary entries from both sides. Entries come from two sources:
- in-memory (added directly via `add`, e.g. by the in-process runner), and
- per-side JSONL files written by CommentaryEmitters (read on demand).

`entries()` merges, filters, and sorts everything by timestamp.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable


class FeedStore:
    def __init__(self, jsonl_paths: Iterable[Path] | None = None) -> None:
        self.jsonl_paths = [Path(p) for p in (jsonl_paths or [])]
        self._memory: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._memory.append(dict(entry))

    def _read_files(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.jsonl_paths:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def entries(
        self,
        side: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            combined = list(self._memory) + self._read_files()
        if side:
            combined = [e for e in combined if e.get("side") == side]
        if since is not None:
            combined = [e for e in combined if float(e.get("ts", 0)) > since]
        combined.sort(key=lambda e: float(e.get("ts", 0)))
        if limit is not None:
            combined = combined[-limit:]
        return combined

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
