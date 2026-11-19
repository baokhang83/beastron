"""Checks: what decides whether an eval run passed.

Every check is written in the task's `[[checks]]` list and evaluated after the
run, against the workspace, the agent's final answer, and a snapshot of the
files taken *before* the run:

| type            | fields                          | passes when                                              |
|-----------------|---------------------------------|----------------------------------------------------------|
| `command`       | `run`, `timeout`, `exit_code`   | the command exits with `exit_code` (default 0)           |
| `file_exists`   | `path`                          | the file exists                                          |
| `file_absent`   | `path`                          | the file doesn't exist                                   |
| `contains`      | `path`, `text`, `regex`         | the file contains the text (or matches the regex)        |
| `not_contains`  | `path`, `text`, `regex`         | it doesn't                                               |
| `unchanged`     | `paths`                         | these files or folders are exactly as before the run     |
| `no_changes`    | `ignore`                        | no file changed at all (for questions)                   |
| `only_changed`  | `paths`                         | nothing changed outside these paths (`*` globs allowed)  |
| `answer_mentions` | `patterns`                    | the final answer matches every regex (case-insensitive)  |

Checks are written by task authors, not by the model, so `command` runs
without the sandbox the agent's own shell commands use.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..tools.files import IGNORED_DIRS

Snapshot = dict[str, str]  # relative path -> sha256 of the content

COMMAND_TIMEOUT = 300
REASON_TAIL = 600  # characters of command output kept in a failure reason


class CheckError(Exception):
    """A check is written incorrectly (a task authoring error)."""


@dataclass(frozen=True)
class Check:
    type: str
    path: str | None = None
    paths: tuple[str, ...] = ()
    text: str | None = None
    regex: bool = False
    run: str | None = None
    timeout: int = COMMAND_TIMEOUT
    exit_code: int = 0
    patterns: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    check: Check
    passed: bool
    reason: str  # why it failed, or a short confirmation


@dataclass
class Context:
    root: Path
    answer: str = ""
    before: Snapshot = field(default_factory=dict)


# ----- parsing --------------------------------------------------------------

_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    # type: (required fields, optional fields)
    "command": ({"run"}, {"timeout", "exit_code"}),
    "file_exists": ({"path"}, set()),
    "file_absent": ({"path"}, set()),
    "contains": ({"path", "text"}, {"regex"}),
    "not_contains": ({"path", "text"}, {"regex"}),
    "unchanged": ({"paths"}, set()),
    "no_changes": (set(), {"ignore"}),
    "only_changed": ({"paths"}, set()),
    "answer_mentions": ({"patterns"}, set()),
}
TYPES = tuple(_FIELDS)


def parse_checks(raw: list[dict[str, Any]]) -> list[Check]:
    return [_parse(i, entry) for i, entry in enumerate(raw, 1)]


def _parse(i: int, entry: dict[str, Any]) -> Check:
    kind = entry.get("type")
    if kind not in _FIELDS:
        raise CheckError(f"check {i}: type {kind!r} is not one of {list(TYPES)}")
    required, optional = _FIELDS[kind]
    fields = set(entry) - {"type"}
    missing = required - fields
    if missing:
        raise CheckError(f"check {i} ({kind}): missing {sorted(missing)}")
    unknown = fields - required - optional
    if unknown:
        raise CheckError(f"check {i} ({kind}): unknown fields {sorted(unknown)}; allowed: {sorted(required | optional)}")

    def text(name: str) -> str:
        value = entry[name]
        if not isinstance(value, str) or not value:
            raise CheckError(f"check {i} ({kind}): '{name}' must be non-empty text")
        return value

    def text_list(name: str) -> tuple[str, ...]:
        value = entry.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise CheckError(f"check {i} ({kind}): '{name}' must be a list of non-empty text")
        return tuple(value)

    def number(name: str, default: int) -> int:
        value = entry.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CheckError(f"check {i} ({kind}): '{name}' must be a whole number >= 0")
        return value

    kwargs: dict[str, Any] = {"type": kind}
    if "path" in entry:
        kwargs["path"] = _relative(i, kind, text("path"))
    if "paths" in entry:
        kwargs["paths"] = tuple(_relative(i, kind, p) for p in text_list("paths"))
        if not kwargs["paths"]:
            raise CheckError(f"check {i} ({kind}): 'paths' can't be empty")
    if "text" in entry:
        kwargs["text"] = text("text")
    if "regex" in entry:
        if not isinstance(entry["regex"], bool):
            raise CheckError(f"check {i} ({kind}): 'regex' must be true or false")
        kwargs["regex"] = entry["regex"]
        _compile(i, kind, kwargs["text"])
    if "run" in entry:
        kwargs["run"] = text("run")
        kwargs["timeout"] = number("timeout", COMMAND_TIMEOUT) or COMMAND_TIMEOUT
        kwargs["exit_code"] = number("exit_code", 0)
    if "patterns" in entry:
        kwargs["patterns"] = text_list("patterns")
        if not kwargs["patterns"]:
            raise CheckError(f"check {i} ({kind}): 'patterns' can't be empty")
        for pattern in kwargs["patterns"]:
            _compile(i, kind, pattern)
    if "ignore" in entry:
        kwargs["ignore"] = tuple(_relative(i, kind, p) for p in text_list("ignore"))
    return Check(**kwargs)


def _relative(i: int, kind: str, path: str) -> str:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise CheckError(f"check {i} ({kind}): path '{path}' must stay inside the workspace")
    return str(pure)


def _compile(i: int, kind: str, pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as e:
        raise CheckError(f"check {i} ({kind}): invalid regex {pattern!r}: {e}") from None


# ----- snapshots --------------------------------------------------------------

def snapshot(root: Path) -> Snapshot:
    """Content hashes of the workspace's files, skipping .git, generated folders (caches, builds),
    and, in a git workspace, everything git ignores (e.g. build output such as dependency-reduced-pom.xml)."""
    result: Snapshot = {}
    ignored_files, ignored_dirs = _git_ignored(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        # skip ignored folders without walking into them (.git can be huge)
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.endswith(".egg-info") and prefix + d + "/" not in ignored_dirs
        ]
        for name in filenames:
            if name in IGNORED_DIRS or prefix + name in ignored_files:
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[rel] = "symlink:" + str(path.readlink())
            elif path.is_file():
                result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(result.items()))


def _git_ignored(root: Path) -> tuple[set[str], set[str]]:
    """Paths git ignores in the workspace: (files, folders ending in "/"). Empty outside git."""
    if not (root / ".git").exists():
        return set(), set()
    try:
        proc = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files", "--others", "--ignored",
             "--exclude-standard", "--directory", "-z"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set(), set()
    if proc.returncode != 0:
        return set(), set()
    paths = [p.decode(errors="replace") for p in proc.stdout.split(b"\0") if p]
    return {p for p in paths if not p.endswith("/")}, {p for p in paths if p.endswith("/")}


def changed_paths(before: Snapshot, after: Snapshot) -> list[str]:
    """Files created, modified or deleted between two snapshots."""
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


# ----- evaluation ---------------------------------------------------------------

def evaluate(checks: list[Check], ctx: Context) -> list[CheckResult]:
    after = snapshot(ctx.root)
    return [_evaluate(check, ctx, after) for check in checks]


def _evaluate(check: Check, ctx: Context, after: Snapshot) -> CheckResult:
    def result(passed: bool, reason: str) -> CheckResult:
        return CheckResult(check, passed, reason)

    root = ctx.root
    match check.type:
        case "command":
            try:
                proc = subprocess.run(
                    check.run, shell=True, cwd=root, capture_output=True, text=True, errors="replace",
                    timeout=check.timeout, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                return result(False, f"`{check.run}` timed out after {check.timeout}s")
            if proc.returncode == check.exit_code:
                return result(True, f"`{check.run}` exited with {proc.returncode}")
            tail = (proc.stdout + proc.stderr).strip()[-REASON_TAIL:]
            return result(False, f"`{check.run}` exited with {proc.returncode}, expected {check.exit_code}:\n{tail}")

        case "file_exists":
            exists = (root / check.path).exists()
            return result(exists, f"{check.path} {'exists' if exists else 'does not exist'}")

        case "file_absent":
            exists = (root / check.path).exists()
            return result(not exists, f"{check.path} {'still exists' if exists else 'is absent'}")

        case "contains" | "not_contains":
            path = root / check.path
            if not path.is_file():
                return result(check.type == "not_contains", f"{check.path} does not exist")
            content = path.read_text(encoding="utf-8", errors="replace")
            if check.regex:
                found = re.search(check.text, content, re.IGNORECASE | re.MULTILINE) is not None
            else:
                found = check.text in content
            what = f"/{check.text}/" if check.regex else repr(check.text)
            if check.type == "contains":
                return result(found, f"{check.path} {'contains' if found else 'does not contain'} {what}")
            return result(not found, f"{check.path} {'still contains' if found else 'does not contain'} {what}")

        case "unchanged":
            changed = [p for p in changed_paths(ctx.before, after) if _under_any(p, check.paths)]
            if changed:
                return result(False, "changed but must stay unchanged: " + ", ".join(changed))
            return result(True, "unchanged: " + ", ".join(check.paths))

        case "no_changes":
            changed = [p for p in changed_paths(ctx.before, after) if not _matches_any(p, check.ignore)]
            if changed:
                return result(False, "files changed although none may: " + ", ".join(changed))
            return result(True, "no files changed")

        case "only_changed":
            outside = [p for p in changed_paths(ctx.before, after) if not _matches_any(p, check.paths)]
            if outside:
                return result(False, "changed outside the allowed paths: " + ", ".join(outside))
            return result(True, "only allowed paths changed")

        case "answer_mentions":
            missing = [p for p in check.patterns if not re.search(p, ctx.answer, re.IGNORECASE | re.MULTILINE)]
            if missing:
                return result(False, "the answer doesn't mention: " + ", ".join(f"/{p}/" for p in missing))
            return result(True, "the answer mentions every required fact")

    raise CheckError(f"unknown check type {check.type!r}")  # parse_checks prevents this


def _under_any(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """A path is allowed when it equals a pattern, lies under a folder pattern, or matches a glob."""
    return any(_under_any(path, (p,)) or fnmatch.fnmatch(path, p) for p in patterns)
