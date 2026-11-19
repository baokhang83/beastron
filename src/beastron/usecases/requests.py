"""The common record for one request a person typed to a coding agent."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..trace import state_dir

# The person immediately correcting the agent: a sign the previous request went wrong.
_CORRECTION = re.compile(
    r"^\s*(no\b|nope\b|wrong\b|that'?s (wrong|not)|not what|undo\b|revert\b|stop\b|don'?t\b|why did you)",
    re.IGNORECASE,
)


@dataclass
class Request:
    source: str  # "claude" or "codex"
    session: str
    project: str | None  # the working folder of the session
    timestamp: str | None
    text: str
    position: int  # 0 for the first request of a session
    command: str | None = None  # a slash command such as "/loop"
    tools: dict[str, int] = field(default_factory=dict)  # tool name -> calls made in response
    shell: dict[str, int] = field(default_factory=dict)  # first word of shell commands -> count, e.g. git, mvn
    corrected: bool = False  # the next request looks like a correction


def usecases_dir() -> Path:
    return state_dir() / "usecases"


def finish_session(requests: list[Request]) -> list[Request]:
    """Mark requests that the person immediately corrected."""
    for previous, current in zip(requests, requests[1:]):
        if _CORRECTION.match(current.text):
            previous.corrected = True
    return requests


def add_tool_use(request: Request, name: str, shell_command: str | None = None) -> None:
    request.tools[name] = request.tools.get(name, 0) + 1
    if shell_command:
        for word in _shell_words(shell_command):
            request.shell[word] = request.shell.get(word, 0) + 1


_SHELL_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "in",
    "function", "return", "export", "local", "set", "unset", "time", "!", "{", "}", "(", ")", "[", "[[",
}
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")


def _shell_words(command: str) -> list[str]:
    """First word of each command in a shell line: `cd x && git status | head` -> [cd, git, head].

    Heredoc bodies (`cat <<EOF ... EOF`) are script text, not commands, so
    everything after the first `<<` is ignored, and only words that look like
    command names count.
    """
    command = command.split("<<", 1)[0]
    words = []
    for part in re.split(r"&&|\|\||[;|\n]", command):
        tokens = part.strip().split()
        while tokens and ("=" in tokens[0] and not tokens[0].startswith(("-", "/")) or tokens[0] in _SHELL_KEYWORDS):
            tokens.pop(0)  # FOO=bar cmd, or a keyword before the command
        if tokens:
            name = Path(tokens[0]).name
            if _COMMAND_NAME.match(name):
                words.append(name)
    return words


def save(requests: list[Request], name: str) -> Path:
    """Write requests to the local use-case folder (never inside a project)."""
    folder = usecases_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for request in requests:
            f.write(json.dumps(asdict(request), ensure_ascii=False) + "\n")
    path.chmod(0o600)  # prompts can contain anything: readable by the owner only
    return path


def load(path: Path) -> list[Request]:
    return [Request(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stats(requests: list[Request]) -> dict[str, Any]:
    tools: Counter[str] = Counter()
    for r in requests:
        tools.update(r.tools)
    return {
        "requests": len(requests),
        "sessions": len({r.session for r in requests}),
        "projects": len({r.project for r in requests if r.project}),
        "commands": sum(1 for r in requests if r.command),
        "corrected": sum(r.corrected for r in requests),
        "top_tools": dict(tools.most_common(8)),
    }
