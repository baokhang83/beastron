"""Read Claude Code's session logs into requests.

Logs live in `~/.claude/projects/<encoded working folder>/<session>.jsonl`
(`BEASTRON_CLAUDE_DIR` overrides the root). Each line is a record; the ones that
matter are:
- `user` records whose content is text: a request the person typed (or a slash
  command). User records whose content is a list of `tool_result` blocks are
  tool output, not requests.
- `assistant` records: their `tool_use` blocks are the tools used in response.

Skipped: meta records, sidechains (sub-agents), compaction summaries, messages
the harness generates (task notifications, local command output), sessions of
plugin observers, and projects on the exclusion list. Excluded projects are
recognized by their folder name, so their files are never opened.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..evals.exclude import excluded_paths, is_excluded
from .requests import Request, add_tool_use, finish_session

# Folders of sessions that aren't a person working, e.g. the claude-mem plugin's observer.
SKIPPED_FOLDERS = ("claude-mem-observer",)
# Messages the harness writes into the user channel.
HARNESS_PREFIXES = (
    "<task-notification", "<local-command-stdout", "<local-command-stderr", "<local-command-caveat",
    "<system-reminder", "This session is being continued from a previous conversation",
    "[Request interrupted", "<<autonomous-loop",
)
_COMMAND = re.compile(r"<command-name>\s*(/?[\w:.-]+)\s*</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_PASTE = re.compile(r"</?pasted_content[^>]*>")


def claude_root() -> Path:
    return Path(os.environ.get("BEASTRON_CLAUDE_DIR") or "~/.claude/projects").expanduser()


def encode_folder(path: Path) -> str:
    """How Claude Code names a project's log folder: every "/" and "." becomes "-"."""
    return re.sub(r"[/.]", "-", str(path))


def session_files(root: Path | None = None) -> Iterator[Path]:
    root = root or claude_root()
    if not root.is_dir():
        return
    excluded = {encode_folder(p) for p in excluded_paths()}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or any(s in folder.name for s in SKIPPED_FOLDERS):
            continue
        if any(folder.name == e or folder.name.startswith(e + "-") for e in excluded):
            continue  # recognized by name: the files are never opened
        yield from sorted(folder.glob("*.jsonl"))


def read_session(path: Path) -> list[Request]:
    requests: list[Request] = []
    current: Request | None = None
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("isSidechain") or record.get("isMeta") or record.get("isCompactSummary"):
            continue
        cwd = record.get("cwd")
        if cwd and is_excluded(Path(cwd)):
            return []  # a session that ran in an excluded project, whatever its folder name
        kind = record.get("type")
        content = (record.get("message") or {}).get("content")
        if kind == "user":
            text, command = _request_text(content)
            if text is None:
                continue
            current = Request(
                source="claude", session=path.stem, project=cwd, timestamp=record.get("timestamp"),
                text=text, position=len(requests), command=command,
            )
            requests.append(current)
        elif kind == "assistant" and current is not None and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_input = block.get("input") or {}
                    shell = tool_input.get("command") if block.get("name") == "Bash" else None
                    add_tool_use(current, block.get("name", "?"), shell if isinstance(shell, str) else None)
    return finish_session(requests)


def read_all(root: Path | None = None) -> list[Request]:
    requests = []
    for path in session_files(root):
        requests += read_session(path)
    return requests


def _request_text(content: Any) -> tuple[str | None, str | None]:
    """The typed text and slash command of a user record, or (None, None) if it isn't a request."""
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None, None
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        return None, None
    stripped = content.lstrip()
    if stripped.startswith(HARNESS_PREFIXES):
        return None, None
    command = _COMMAND.search(content)
    if command:
        args = _COMMAND_ARGS.search(content)
        return (args.group(1).strip() if args else ""), command.group(1)
    if stripped.startswith("<command-message>"):
        return None, None  # the expanded body of a command; the command itself was already recorded
    return _PASTE.sub("", content).strip(), None
