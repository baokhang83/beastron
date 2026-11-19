"""Read Codex session logs into requests (the same format as the Claude Code reader).

Logs live in `~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`
(`BEASTRON_CODEX_DIR` overrides the root). Every line is `{timestamp, type, payload}`:
- `session_meta` (first line): the session id and its working folder (`cwd`)
- `event_msg` with `payload.type == "user_message"`: a request the person typed.
  (`response_item` user messages also contain context Codex injects itself,
  so they are not used.)
- `response_item` tool calls: `custom_tool_call` `exec` is a small script whose
  `tools.exec_command({cmd: "..."})` calls run shell commands; `exec_command`
  function calls carry the command as JSON; `apply_patch` edits files.
- `turn_context`: can change the working folder mid-session.

Codex stores sessions by date, not by project, so for an excluded project only
the first line (the session's metadata with its folder) is read before the
file is closed.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..evals.exclude import is_excluded
from .requests import Request, add_tool_use, finish_session

_EXEC_CMD = re.compile(r"""\bcmd\s*:\s*("(?:[^"\\]|\\.)*")""")  # tools.exec_command({cmd: "..."})
_EXEC_TOOL = re.compile(r"\btools\.(\w+)\s*\(")


def codex_root() -> Path:
    return Path(os.environ.get("BEASTRON_CODEX_DIR") or "~/.codex/sessions").expanduser()


def session_files(root: Path | None = None) -> Iterator[Path]:
    root = root or codex_root()
    if root.is_dir():
        yield from sorted(root.rglob("*.jsonl"))


def read_session(path: Path) -> list[Request]:
    requests: list[Request] = []
    current: Request | None = None
    with path.open(encoding="utf-8", errors="replace") as f:
        first = _parse(f.readline())
        meta = (first or {}).get("payload") or {}
        if (first or {}).get("type") != "session_meta":
            meta = {}
        project = meta.get("cwd")
        if project and is_excluded(Path(project)):
            return []  # only the metadata line was read
        session = str(meta.get("id") or meta.get("session_id") or path.stem)
        for line in f:
            record = _parse(line)
            if not record:
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            kind, ptype = record.get("type"), payload.get("type")
            if kind == "turn_context" and payload.get("cwd"):
                if is_excluded(Path(payload["cwd"])):
                    return []
                project = payload["cwd"]
            elif kind == "event_msg" and ptype == "user_message":
                text = (payload.get("message") or "").strip()
                if not text:
                    continue
                command = text.split()[0] if text.startswith("/") else None
                current = Request(
                    source="codex", session=session, project=project, timestamp=record.get("timestamp"),
                    text=text, position=len(requests), command=command,
                )
                requests.append(current)
            elif kind == "response_item" and current is not None:
                _tool_calls(current, payload)
    return finish_session(requests)


def read_all(root: Path | None = None) -> list[Request]:
    requests = []
    for path in session_files(root):
        requests += read_session(path)
    return requests


def _tool_calls(request: Request, payload: dict[str, Any]) -> None:
    ptype, name = payload.get("type"), payload.get("name")
    if ptype == "custom_tool_call" and name == "exec" and isinstance(payload.get("input"), str):
        script = payload["input"]
        commands = [_unquote(m.group(1)) for m in _EXEC_CMD.finditer(script)]
        for command in commands:
            add_tool_use(request, "exec_command", command)
        for tool in _EXEC_TOOL.findall(script):
            if tool != "exec_command":
                add_tool_use(request, tool)
        if not commands and not _EXEC_TOOL.search(script):
            add_tool_use(request, "exec")
    elif ptype == "custom_tool_call" and name:
        add_tool_use(request, name)
    elif ptype == "function_call" and name:
        command = None
        if name == "exec_command":
            try:
                command = (json.loads(payload.get("arguments") or "{}") or {}).get("cmd")
            except json.JSONDecodeError:
                pass
        add_tool_use(request, name, command if isinstance(command, str) else None)


def _unquote(literal: str) -> str:
    try:
        return json.loads(literal)
    except json.JSONDecodeError:
        return literal.strip('"')


def _parse(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
