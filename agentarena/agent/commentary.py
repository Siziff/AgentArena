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
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.treasure import DEFAULT_ALPHABET, DEFAULT_LENGTH
from ..core.types import Side
from .provider import _ACTION_RE, _FENCE_RE, strip_json_objects

DEFAULT_MAX_LEN = 600

# whole paired blocks like <tool_call>...</tool_call> (removed with their content)
_BLOCK_RE = re.compile(
    r"<(?:tool_call|tool_response|function_calls?|invoke|tools?|response|"
    r"thinking|thought|reasoning)\b[^>]*>.*?</(?:tool_call|tool_response|"
    r"function_calls?|invoke|tools?|response|thinking|thought|reasoning)\s*>",
    re.IGNORECASE | re.DOTALL,
)
# tool/special-token tags to strip from commentary (e.g. <tool_call>, <|im_start|>)
_TAG_RE = re.compile(
    r"<\|[^>]*\|>|</?(?:tool_call|tool_response|function_calls?|invoke|tools?|"
    r"response|thinking|thought|reasoning)[^>]*>",
    re.IGNORECASE,
)
# leading "COMMENT"/"THOUGHT"/"ACTION" style prefixes the model sometimes adds,
# including garbled variants like "AÇÃO" (mangled "ACTION").
_PREFIX_RE = re.compile(
    r"^\s*(?:COMMENTARY|COMMENT|NOTE|THOUGHT|THINKING|ACTION|A[CÇ][ÃAà]O|ACAO)\b\s*[:\-–]?\s*",
    re.IGNORECASE,
)


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
        secrets: Iterable[str] | str | None = None,
        alphabet: str = DEFAULT_ALPHABET,
        treasure_length: int = DEFAULT_LENGTH,
    ) -> None:
        self.side = side
        self.path = Path(path) if path else None
        self.max_len = max_len
        if secrets is None:
            self._secrets: list[str] = []
        elif isinstance(secrets, str):
            self._secrets = [secrets]
        else:
            self._secrets = [s for s in secrets if s]
        self._pattern = _redaction_pattern(alphabet, treasure_length)
        self.entries: list[CommentaryEntry] = []
        self._last_text: str | None = None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _clean(self, text: str) -> str:
        text = str(text)
        # drop code fences and ACTION/tool lines entirely
        lines = [
            ln for ln in str(text).splitlines()
            if not _FENCE_RE.match(ln) and not _ACTION_RE.match(ln)
        ]
        text = " ".join(lines)
        # strip any {"name": ...} / JSON objects (command & tool dumps)
        text = strip_json_objects(text)
        # strip whole tool blocks, then lone tool/special-token tags, then prefixes
        text = _BLOCK_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        # strip leading action/comment prefixes (loop to catch stacked ones)
        for _ in range(3):
            text = _PREFIX_RE.sub("", text)
        # collapse whitespace/newlines
        text = " ".join(text.split())
        for secret in self._secrets:
            text = text.replace(secret, "[redacted-secret]")
        text = self._pattern.sub("[redacted]", text)
        if len(text) > self.max_len:
            text = text[: self.max_len - 1].rstrip() + "…"
        return text.strip()

    def emit(self, text: str, phase: str = "unknown") -> CommentaryEntry | None:
        cleaned = self._clean(text)
        # skip empty (e.g. pure JSON/logs) and consecutive duplicates
        if not cleaned or cleaned == self._last_text:
            return None
        self._last_text = cleaned
        entry = CommentaryEntry(side=self.side.value, phase=phase, text=cleaned)
        with self._lock:
            self.entries.append(entry)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry
