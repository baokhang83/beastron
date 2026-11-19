"""Eval task format: `evals/tasks/<id>/task.toml`.

    prompt = "The tests fail. Fix the bug, then run: python -m pytest -q"
    category = "fix"            # see CATEGORIES
    language = "python"
    source = "synthetic"        # synthetic | commit-replay | captured
    timeout = 600               # seconds for the whole run
    max_steps = 25              # model calls
    tags = ["seed", "quick"]    # suites the task belongs to

    [workspace]                 # synthetic / captured:
    fixture = "repo"            #   folder next to task.toml, copied fresh for every run
    setup = "setup.sh"          #   optional script run inside the copy (e.g. to build git history)
    # commit-replay: a public commit, referenced (no code is copied into this repository)
    # url = "https://github.com/baokhang83/blastradius"
    # commit = "1a2b3c4..."     #   the workspace is the repository at this commit's parent
    # repo = "~/Documents/blastradius"   # optional local clone, used instead of cloning url
    # tests = ["src/test/java/x/FooTest.java"]  # taken from the commit: the tests to make pass
    # license = "Apache-2.0"    #   the repository's license, recorded when the task was added
    # at = "commit"             #   start AT the commit instead of its parent (questions about a repository)

    [[checks]]                  # decide pass or fail (types in firox.evals.checks)
    type = "command"
    run = "python -m pytest -q"

Errors name the file and the field, so a broken task is quick to fix.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checks import Check, CheckError, parse_checks

TASKS_DIR = Path("evals/tasks")
SOURCES = ("synthetic", "commit-replay", "captured")
CATEGORIES = (
    "explain",   # answer a question about the code
    "fix",       # fix a bug or a failing test
    "feature",   # add a feature
    "refactor",
    "remove",    # remove or undo something
    "tests",     # write tests
    "build",     # build and CI
    "git",       # git, releases, pull requests
    "docs",
    "config",    # configuration and infrastructure
)
TOP_LEVEL_KEYS = {"prompt", "category", "language", "source", "timeout", "max_steps", "tags", "workspace", "checks"}
WORKSPACE_KEYS = {"fixture", "setup", "repo", "commit", "url", "tests", "license", "at"}


class TaskError(Exception):
    pass


@dataclass(frozen=True)
class WorkspaceSpec:
    fixture: str | None = None
    setup: str | None = None
    repo: Path | None = None  # commit-replay: a local clone
    commit: str | None = None
    url: str | None = None  # commit-replay: the public repository
    tests: tuple[str, ...] = ()  # commit-replay: test files taken from the commit into the workspace
    license: str | None = None
    at: str = "parent"  # commit-replay: start at the commit's "parent" (replay it) or at the "commit" itself


@dataclass(frozen=True)
class Task:
    id: str
    dir: Path
    prompt: str
    category: str
    language: str
    source: str
    workspace: WorkspaceSpec
    checks: list[Check]
    timeout: int = 600
    max_steps: int = 25
    tags: tuple[str, ...] = field(default_factory=tuple)


def load_task(task_dir: Path) -> Task:
    path = task_dir / "task.toml"
    where = str(path)
    if not path.is_file():
        raise TaskError(f"{where}: not found")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise TaskError(f"{where}: invalid TOML: {e}") from e

    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        raise TaskError(f"{where}: unknown keys {sorted(unknown)}. Allowed: {sorted(TOP_LEVEL_KEYS)}")
    for key in ("prompt", "category", "language", "source"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise TaskError(f"{where}: '{key}' is required and must be text")
    if data["category"] not in CATEGORIES:
        raise TaskError(f"{where}: category '{data['category']}' is not one of {list(CATEGORIES)}")
    if data["source"] not in SOURCES:
        raise TaskError(f"{where}: source '{data['source']}' is not one of {list(SOURCES)}")
    for key in ("timeout", "max_steps"):
        if key in data and (not isinstance(data[key], int) or data[key] <= 0):
            raise TaskError(f"{where}: '{key}' must be a positive whole number")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise TaskError(f"{where}: 'tags' must be a list of text")

    workspace = _workspace(where, task_dir, data["source"], data.get("workspace", {}))
    raw_checks = data.get("checks", [])
    if not isinstance(raw_checks, list) or not raw_checks:
        raise TaskError(f"{where}: at least one [[checks]] entry is required")
    for i, check in enumerate(raw_checks, 1):
        if not isinstance(check, dict) or not isinstance(check.get("type"), str):
            raise TaskError(f"{where}: check {i} needs a 'type'")
    try:
        checks = parse_checks(raw_checks)
    except CheckError as e:
        raise TaskError(f"{where}: {e}") from None

    return Task(
        id=task_dir.name,
        dir=task_dir.resolve(),
        prompt=data["prompt"].strip(),
        category=data["category"],
        language=data["language"],
        source=data["source"],
        workspace=workspace,
        checks=checks,
        timeout=data.get("timeout", 600),
        max_steps=data.get("max_steps", 25),
        tags=tuple(tags),
    )


def load_tasks(root: Path = TASKS_DIR, *, tag: str | None = None, ids: list[str] | None = None) -> list[Task]:
    """All tasks under `root`, sorted by id; optionally only one tag (suite) or some ids."""
    if not root.is_dir():
        raise TaskError(f"{root}: no tasks folder")
    tasks = [load_task(d) for d in sorted(root.iterdir()) if (d / "task.toml").is_file()]
    if tag:
        tasks = [t for t in tasks if tag in t.tags]
    if ids:
        missing = set(ids) - {t.id for t in tasks}
        if missing:
            raise TaskError(f"unknown task ids: {sorted(missing)}")
        tasks = [t for t in tasks if t.id in ids]
    return tasks


def _workspace(where: str, task_dir: Path, source: str, raw: Any) -> WorkspaceSpec:
    if not isinstance(raw, dict):
        raise TaskError(f"{where}: [workspace] must be a table")
    unknown = set(raw) - WORKSPACE_KEYS
    if unknown:
        raise TaskError(f"{where}: unknown [workspace] keys {sorted(unknown)}. Allowed: {sorted(WORKSPACE_KEYS)}")
    if source == "commit-replay":
        if not raw.get("commit") or not (raw.get("repo") or raw.get("url")):
            raise TaskError(f"{where}: a commit-replay task needs [workspace] commit, and url or repo")
        if raw.get("fixture") or raw.get("setup"):
            raise TaskError(f"{where}: a commit-replay task can't have a fixture or setup")
        tests = raw.get("tests", [])
        if not isinstance(tests, list) or not all(isinstance(t, str) and t for t in tests):
            raise TaskError(f"{where}: [workspace] tests must be a list of paths")
        at = raw.get("at", "parent")
        if at not in ("parent", "commit"):
            raise TaskError(f"{where}: [workspace] at must be \"parent\" or \"commit\"")
        if at == "commit" and tests:
            raise TaskError(f"{where}: tests are only taken from a commit that is replayed (at = \"parent\")")
        return WorkspaceSpec(
            repo=Path(raw["repo"]).expanduser() if raw.get("repo") else None, commit=str(raw["commit"]),
            url=raw.get("url"), tests=tuple(tests), license=raw.get("license"), at=at,
        )
    fixture = raw.get("fixture", "repo")
    if not (task_dir / fixture).is_dir():
        raise TaskError(f"{where}: fixture folder '{fixture}' not found next to task.toml")
    setup = raw.get("setup")
    if setup and not (task_dir / setup).is_file():
        raise TaskError(f"{where}: setup script '{setup}' not found next to task.toml")
    if any(raw.get(k) for k in ("repo", "commit", "url", "tests", "license", "at")):
        raise TaskError(f"{where}: repo, commit, url, tests, license and at are only for commit-replay tasks")
    return WorkspaceSpec(fixture=fixture, setup=setup)
