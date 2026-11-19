"""Session trace: one JSONL file per session with everything that happened.

The trace is the foundation for evals, debugging and the feedback loop
(docs/plan.md § 5.1). It listens to agent events like the UI does and writes
them to `~/.local/state/firox/sessions/` (or `$FIROX_STATE_DIR/sessions/`).

Line 1 is a header: when and where the session ran, the model profile and the
model's digest (so a changed model is visible), firox's own version, and the
project's git state at the start (HEAD, uncommitted diff, untracked files).
Every following line is one event. Streamed text and reasoning are combined
into one `model_output` record per model step instead of hundreds of pieces.

Traces stay on this machine. They contain your prompts, code and command
output, and are never written inside the project.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .config import ModelProfile
from .events import Event, ModelEnd, ModelStart, ReasoningDelta, TextDelta
from .tools import ToolError, Workspace
from .tools.git import run_git

MAX_DIFF = 500_000  # characters of uncommitted diff kept in the header
MAX_UNTRACKED = 2_000  # untracked files listed in the header
DIGEST_TIMEOUT = 2.0  # seconds; a slow or foreign server must not delay startup


def state_dir() -> Path:
    return Path(os.environ.get("FIROX_STATE_DIR") or "~/.local/state/firox").expanduser()


def sessions_dir() -> Path:
    return state_dir() / "sessions"


class SessionTrace:
    """Event listener that appends each event to the session's JSONL file."""

    def __init__(self, path: Path, header: dict[str, Any]) -> None:
        self.path = path
        self._started = time.monotonic()
        self._step: int | None = None
        self._text: list[str] = []
        self._reasoning: list[str] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO | None = path.open("a", encoding="utf-8")
        self._write({"type": "session", **header})

    @classmethod
    def start(cls, ws: Workspace, profile: ModelProfile) -> SessionTrace:
        now = datetime.now(timezone.utc)
        session_id = uuid.uuid4().hex[:8]
        project = re.sub(r"[^A-Za-z0-9._-]", "_", ws.root.name) or "project"
        name = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{project}_{session_id}.jsonl"
        header = {
            "session_id": session_id,
            "started_at": now.isoformat(),
            "cwd": str(ws.root),
            "firox": firox_version(),
            "model": {
                "profile": profile.name,
                "model": profile.model,
                "base_url": profile.base_url,
                "context": profile.context,
                "effort": profile.effort,
                "digest": model_digest(profile),
            },
            "repo": repo_state(ws),
        }
        return cls(sessions_dir() / name, header)

    def __call__(self, event: Event) -> None:
        """Agent event listener."""
        match event:
            case ModelStart(step=step):
                self._step, self._text, self._reasoning = step, [], []
                self._record(event)
            case TextDelta(text=text):
                self._text.append(text)
            case ReasoningDelta(text=text):
                self._reasoning.append(text)
            case ModelEnd(step=step):
                self._write({
                    "type": "model_output", "t": self._elapsed(), "step": step,
                    "text": "".join(self._text), "reasoning": "".join(self._reasoning),
                })
                self._record(event)
            case _:
                self._record(event)

    def note(self, kind: str, **data: Any) -> None:
        """Something that isn't an agent event, e.g. a model switch or /clear."""
        self._write({"type": "note", "t": self._elapsed(), "kind": kind, **data})

    def close(self) -> None:
        if self._file:
            self._write({"type": "session_end", "t": self._elapsed()})
            self._file.close()
            self._file = None

    def _record(self, event: Event) -> None:
        self._write({"type": _snake(type(event).__name__), "t": self._elapsed(), **dataclasses.asdict(event)})

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._started, 3)

    def _write(self, record: dict[str, Any]) -> None:
        if self._file is None:
            return
        # One line per record, flushed at once: a crash loses at most the line being written.
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()


def fan_out(*listeners: Callable[[Event], None] | None) -> Callable[[Event], None]:
    """One event listener that passes every event to several."""
    active = [listener for listener in listeners if listener is not None]

    def emit(event: Event) -> None:
        for listener in active:
            listener(event)

    return emit


def firox_version() -> dict[str, Any]:
    """firox's package version, plus the git commit when running from a checkout."""
    source = Path(__file__).resolve().parents[2]
    info: dict[str, Any] = {"version": __version__, "commit": None, "dirty": None}
    if (source / ".git").exists():
        try:
            head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
            status = subprocess.run(
                ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
                capture_output=True, text=True, timeout=5,
            )
            if head.returncode == 0:
                info["commit"] = head.stdout.strip()
                info["dirty"] = bool(status.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return info


def repo_state(ws: Workspace) -> dict[str, Any] | None:
    """The project's git state at the start of the session, or None if it isn't a git repo."""
    try:
        if run_git(ws, ["rev-parse", "--is-inside-work-tree"], check=False).stdout.strip() != "true":
            return None
        head = run_git(ws, ["rev-parse", "HEAD"], check=False)
        branch = run_git(ws, ["branch", "--show-current"], check=False).stdout.strip()
        status = run_git(ws, ["status", "--porcelain=v1", "--untracked-files=all"]).stdout
        diff = run_git(ws, ["diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD"], check=False).stdout
    except ToolError:
        return None
    untracked = []
    for line in status.splitlines():
        if line.startswith("?? ") and len(untracked) < MAX_UNTRACKED:
            path = line[3:].strip().strip('"')
            untracked.append({"path": path, **_file_fingerprint(ws.root / path)})
    return {
        "head": head.stdout.strip() if head.returncode == 0 else None,  # None: no commits yet
        "branch": branch or None,
        "status": status,
        "diff": diff[:MAX_DIFF],
        "diff_truncated": len(diff) > MAX_DIFF,
        "untracked": untracked,
    }


def model_digest(profile: ModelProfile) -> str | None:
    """The digest of the exact model file, as reported by Ollama; None for other servers.

    Ollama's native API lives next to its OpenAI-compatible one (`.../v1`).
    `/api/tags` lists the installed models with their digests.
    """
    base = profile.base_url.rstrip("/")
    if not base.endswith("/v1"):
        return None
    try:
        with urllib.request.urlopen(base[: -len("/v1")] + "/api/tags", timeout=DIGEST_TIMEOUT) as response:
            return digest_from_tags(json.loads(response.read()), profile.model)
    except (OSError, ValueError):
        return None


def digest_from_tags(tags: dict[str, Any], model: str) -> str | None:
    """Find a model's digest in Ollama's /api/tags answer. A name without a tag means ":latest", as in Ollama."""
    wanted = model if ":" in model else model + ":latest"
    for entry in tags.get("models", []):
        if entry.get("name") == wanted or entry.get("model") == wanted:
            return entry.get("digest")
    return None


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            return {"size": None, "sha256": None}
        data = path.read_bytes()
    except OSError:
        return {"size": None, "sha256": None}
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
