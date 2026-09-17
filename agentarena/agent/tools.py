"""The agent's tool set.

Tools are the agent's only way to act on the world. File and shell operations
are sandboxed to the agent's own workspace (the opponent is reached over the
network or via an explicitly *exposed* read-only directory). True isolation is
provided by containers in real matches (see docs/RULES.md).
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.types import Side
from ..judge.client import JudgeClient
from .commentary import CommentaryEmitter
from .provider import ToolCall, ToolSpec

MAX_OUTPUT = 4000


@dataclass
class SideContext:
    """Everything a tool handler needs to act on behalf of one side."""

    side: Side
    workspace: Path
    judge: JudgeClient
    commentary: CommentaryEmitter
    opponent_exposed: Path | None = None  # read-only view of opponent (local mode)
    shell_timeout: float = 20.0
    http_timeout: float = 15.0
    max_output: int = MAX_OUTPUT

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        if self.opponent_exposed is not None:
            self.opponent_exposed = Path(self.opponent_exposed).resolve()


# ---- tool schemas ----------------------------------------------------------

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="run_shell",
        description=(
            "Run a shell command in your own workspace (cwd = your side). Use it for "
            "crypto, packaging, networking, inspection, etc. Returns exit code and "
            "truncated stdout/stderr."
        ),
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="read_file",
        description="Read a file from your workspace or an exposed opponent path.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_file",
        description="Write/overwrite a file inside your own workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="list_dir",
        description="List the entries of a directory in your workspace or an exposed opponent path.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="http_request",
        description="Make an HTTP request (probe an opponent service or call the Judge).",
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "HEAD"]},
                "url": {"type": "string"},
                "headers": {"type": "object"},
                "body": {"type": "string"},
            },
            "required": ["method", "url"],
        },
    ),
    ToolSpec(
        name="submit_code",
        description=(
            "Submit a candidate opponent code to the Judge. This is the ONLY way to win. "
            "Rate-limited; a 429 means slow down."
        ),
        parameters={
            "type": "object",
            "properties": {"guess": {"type": "string"}},
            "required": ["guess"],
        },
    ),
    ToolSpec(
        name="comment",
        description=(
            "Emit a one-sentence English summary of what you are doing / just did. "
            "Call this after every action."
        ),
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    ToolSpec(
        name="finish",
        description="End the current iteration / yield your turn.",
        parameters={
            "type": "object",
            "properties": {"note": {"type": "string"}},
        },
    ),
]


# ---- path sandboxing -------------------------------------------------------


def _resolve(ctx: SideContext, path: str, writable: bool) -> Path:
    """Resolve `path` and ensure it stays within allowed roots.

    Relative paths resolve against the workspace. Reads may also target the
    opponent's exposed directory; writes are confined to the workspace.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ctx.workspace / candidate
    resolved = candidate.resolve()

    roots = [ctx.workspace]
    if not writable and ctx.opponent_exposed is not None:
        roots.append(ctx.opponent_exposed)

    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PermissionError(
        f"path {path!r} escapes allowed roots ({'write' if writable else 'read'})"
    )


def _truncate(ctx: SideContext, text: str) -> str:
    if len(text) > ctx.max_output:
        return text[: ctx.max_output] + f"\n... [truncated {len(text) - ctx.max_output} chars]"
    return text


# ---- tool handlers ---------------------------------------------------------


def _run_shell(ctx: SideContext, args: dict[str, Any]) -> str:
    command = str(args.get("command", ""))
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ctx.workspace),
            capture_output=True,
            text=True,
            timeout=ctx.shell_timeout,
        )
        out = f"exit_code={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        return _truncate(ctx, out)
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {ctx.shell_timeout}s"
    except Exception as exc:  # pragma: no cover - defensive
        return f"error: {exc}"


def _read_file(ctx: SideContext, args: dict[str, Any]) -> str:
    try:
        path = _resolve(ctx, str(args.get("path", "")), writable=False)
        if not path.is_file():
            return f"error: not a file: {path}"
        return _truncate(ctx, path.read_text(encoding="utf-8", errors="replace"))
    except (PermissionError, OSError) as exc:
        return f"error: {exc}"


def _write_file(ctx: SideContext, args: dict[str, Any]) -> str:
    try:
        path = _resolve(ctx, str(args.get("path", "")), writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return f"ok: wrote {len(content)} bytes to {path.relative_to(ctx.workspace)}"
    except (PermissionError, OSError) as exc:
        return f"error: {exc}"


def _list_dir(ctx: SideContext, args: dict[str, Any]) -> str:
    try:
        path = _resolve(ctx, str(args.get("path", ".")), writable=False)
        if not path.is_dir():
            return f"error: not a directory: {path}"
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        return _truncate(ctx, "\n".join(entries) or "(empty)")
    except (PermissionError, OSError) as exc:
        return f"error: {exc}"


def _http_request(ctx: SideContext, args: dict[str, Any]) -> str:
    method = str(args.get("method", "GET")).upper()
    url = str(args.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return "error: only http:// and https:// URLs are allowed"
    headers = {str(k): str(v) for k, v in (args.get("headers") or {}).items()}
    body = args.get("body")
    data = body.encode("utf-8") if isinstance(body, str) else None
    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=ctx.http_timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return _truncate(ctx, f"status={resp.status}\n{text}")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return _truncate(ctx, f"status={exc.code}\n{text}")
    except Exception as exc:
        return f"error: {exc}"


def _submit_code(ctx: SideContext, args: dict[str, Any]) -> str:
    guess = str(args.get("guess", ""))
    result = ctx.judge.submit(guess)
    return json.dumps(result, ensure_ascii=False)


def _comment(ctx: SideContext, args: dict[str, Any], phase: str) -> str:
    entry = ctx.commentary.emit(str(args.get("text", "")), phase=phase)
    return f"ok: commentary recorded ({entry.side}/{entry.phase})"


def _finish(ctx: SideContext, args: dict[str, Any]) -> str:
    note = str(args.get("note", ""))
    return f"ok: finished iteration{': ' + note if note else ''}"


# ---- dispatcher ------------------------------------------------------------


def execute_tool(ctx: SideContext, call: ToolCall, phase: str = "unknown") -> str:
    """Execute one tool call within the side context and return a string result."""
    name = call.name
    args = call.arguments or {}
    if name == "run_shell":
        return _run_shell(ctx, args)
    if name == "read_file":
        return _read_file(ctx, args)
    if name == "write_file":
        return _write_file(ctx, args)
    if name == "list_dir":
        return _list_dir(ctx, args)
    if name == "http_request":
        return _http_request(ctx, args)
    if name == "submit_code":
        return _submit_code(ctx, args)
    if name == "comment":
        return _comment(ctx, args, phase)
    if name == "finish":
        return _finish(ctx, args)
    return f"error: unknown tool {name!r}"
