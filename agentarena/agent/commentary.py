"""Commentary emission for the Arena feed.

Agents describe what they are doing in short plain-English lines. Entries are
kept in memory and (optionally) appended to a JSONL file that the Arena feed
server reads. As a safety net, any exact treasure-length token is redacted so
an agent cannot accidentally leak a secret through commentary.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.treasure import DEFAULT_ALPHABET, DEFAULT_LENGTH
from ..core.types import Side

DEFAULT_MAX_LEN = 280


def _redaction_pattern(alphabet: str, length: int) -> re.Pattern[str]:
    cls = re.escape(alphabet)
    return re.compile(rf"(?<![{cls}])[{cls}]{{{length}}}(?![{cls}])")


@dataclass
class CommentaryEntry:
    side: str
    phase: str
    text: str
    ts: float = field(default_factory=time.time)


class CommentaryEmitter:
    """Collects commentary for one side and mirrors it to a JSONL file."""

    def __init__(
        self,
        side: Side,
        path: Path | None = None,
        max_len: int = DEFAULT_MAX_LEN,
        secret: str | None = None,
        alphabet: str = DEFAULT_ALPHABET,
        treasure_length: int = DEFAULT_LENGTH,
    ) -> None:
        self.side = side
        self.path = Path(path) if path else None
        self.max_len = max_len
        self._secret = secret
        self._pattern = _redaction_pattern(alphabet, treasure_length)
        self.entries: list[CommentaryEntry] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _clean(self, text: str) -> str:
        text = " ".join(str(text).split())  # collapse whitespace/newlines
        if self._secret:
            text = text.replace(self._secret, "[redacted-secret]")
        text = self._pattern.sub("[redacted]", text)
        if len(text) > self.max_len:
            text = text[: self.max_len - 1].rstrip() + "…"
        return text

    def emit(self, text: str, phase: str = "unknown") -> CommentaryEntry:
        entry = CommentaryEntry(
            side=self.side.value, phase=phase, text=self._clean(text)
        )
        with self._lock:
            self.entries.append(entry)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry
