"""Fresh, reproducible starting points for eval runs.

Every run gets its own temporary folder, so one run can never affect the next:
- synthetic / captured: a copy of the task's fixture folder, then the optional
  setup script (for example to build a git history the task needs)
- commit-replay: a local clone of the repository, checked out at the parent of
  the task's commit, which is exactly the state before the real change
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..tools.git import SAFE_CONFIG
from .exclude import is_excluded
from .task import Task

SETUP_TIMEOUT = 120
# Fixed identities and dates, so a setup script builds the same history every time.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "firox eval", "GIT_AUTHOR_EMAIL": "eval@firox.invalid",
    "GIT_COMMITTER_NAME": "firox eval", "GIT_COMMITTER_EMAIL": "eval@firox.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T12:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T12:00:00Z",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class PreparedWorkspace:
    root: Path  # the project folder the agent works in
    base_commit: str | None  # commit-replay: the parent commit the workspace starts from
    target_commit: str | None  # commit-replay: the real commit the task replays


@contextmanager
def prepared(task: Task, *, keep: bool = False) -> Iterator[PreparedWorkspace]:
    """Create the task's workspace in a new temp folder; remove it afterwards unless `keep`."""
    parent = Path(tempfile.mkdtemp(prefix=f"firox-eval-{task.id}-"))
    try:
        yield create(task, parent / "project")
    finally:
        if not keep:
            shutil.rmtree(parent, ignore_errors=True)


def create(task: Task, dest: Path) -> PreparedWorkspace:
    if dest.exists():
        raise WorkspaceError(f"{dest} already exists")
    if task.source == "commit-replay":
        return _commit_replay(task, dest)
    shutil.copytree(task.dir / task.workspace.fixture, dest, symlinks=True)
    if task.workspace.setup:
        _run(["sh", str(task.dir / task.workspace.setup)], dest, f"setup script of task {task.id}")
    return PreparedWorkspace(dest.resolve(), None, None)


def _commit_replay(task: Task, dest: Path) -> PreparedWorkspace:
    repo = task.workspace.repo.resolve()
    if is_excluded(repo):
        raise WorkspaceError(f"{repo} is on the exclusion list; it can't be used for eval tasks")
    if not (repo / ".git").exists():
        raise WorkspaceError(f"{repo} is not a git repository")
    target = _git(repo, "rev-parse", "--verify", f"{task.workspace.commit}^{{commit}}")
    base = _git(repo, "rev-parse", "--verify", f"{target}^")  # fails clearly for a root commit
    _run(["git", *SAFE_CONFIG, "clone", "--quiet", "--local", "--no-checkout", str(repo), str(dest)], repo, "git clone")
    _run(["git", *SAFE_CONFIG, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", base], dest, "git checkout")
    if _git(dest, "rev-parse", "HEAD") != base:
        raise WorkspaceError(f"workspace for {task.id} is not at {base}")
    return PreparedWorkspace(dest.resolve(), base, target)


def _git(cwd: Path, *args: str) -> str:
    return _run(["git", *SAFE_CONFIG, *args], cwd, f"git {args[0]}").strip()


def _run(cmd: list[str], cwd: Path, what: str) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=SETUP_TIMEOUT,
            stdin=subprocess.DEVNULL, env={**os.environ, **GIT_ENV},
        )
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"{what} took longer than {SETUP_TIMEOUT}s") from None
    if proc.returncode != 0:
        raise WorkspaceError(f"{what} failed: {(proc.stderr or proc.stdout).strip()[:500]}")
    return proc.stdout
