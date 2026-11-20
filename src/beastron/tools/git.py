"""Read-only git access for the model, without approval prompts.

Small models rarely think of running `git status` through run_shell, so tasks
like "undo my latest changes" fail. A dedicated tool makes git visible to them.

Safety: this tool runs with no approval, so it must never write anything or
run programs. Only fixed subcommands are allowed, arguments are passed as a
list (no shell), refs can't start with "-" (that would let the model inject
options such as `--output=FILE`), and repository config that can run programs
(external diff tools and textconv via --no-ext-diff/--no-textconv, fsmonitor
via config) is switched off. One thing remains: clean/smudge filter drivers
(such as git-lfs) configured in the repository still run, exactly as they do
when you run git yourself in that repository.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .base import Args, Tool, ToolError, ToolResult
from .sandbox import Workspace

TIMEOUT = 30
MAX_OUTPUT = 30_000
DEFAULT_LOG = 10
MAX_LOG = 50
_REF = re.compile(r"^[A-Za-z0-9._/~^@{}:+-]+$")

# Config overrides that stop a repository's own config from running programs.
SAFE_CONFIG = [
    "-c", "core.fsmonitor=false",
    "-c", "core.pager=cat",
    "-c", "color.ui=false",
]
_SAFE_CONFIG = SAFE_CONFIG  # also used by eval workspaces (beastron.evals.workspace)


def run_git(ws: Workspace, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(
            ["git", *_SAFE_CONFIG, "-C", str(ws.root), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError:
        raise ToolError("git is not installed.") from None
    except subprocess.TimeoutExpired:
        raise ToolError(f"git {args[0]} took longer than {TIMEOUT}s.") from None
    if check and proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        if "not a git repository" in err:
            raise ToolError("This project is not a git repository, so there is no history to look at.")
        raise ToolError(f"git {args[0]} failed: {err[:500]}")
    return proc


def is_tracked(ws: Workspace, path: Path) -> bool | None:
    """True/False if `path` is/isn't tracked by git; None if the project isn't a git repo."""
    try:
        proc = run_git(ws, ["ls-files", "--error-unmatch", "--", ws.rel(path)], check=False)
    except ToolError:
        return None
    if proc.returncode == 0:
        return True
    if "not a git repository" in proc.stderr:
        return None
    return False


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n[... output cut at {MAX_OUTPUT} characters; narrow it with path or ref]"


# Flags a model may type after the subcommand. Harmless, and already what the tool does;
# anything else is refused, since this tool runs without approval (see the module docstring).
_TYPED_FLAGS_IGNORED = {"--oneline", "--stat", "--patch", "-p", "--no-color", "--no-pager", "--no-ext-diff", "--no-textconv"}
_SUBCOMMANDS = ("status", "diff", "log", "show")


def parse_typed_command(ws: Workspace, text: str, args: Args) -> Args:
    """Turn `diff HEAD~1 -- totals.py` or `git log -5` typed into `command` into the tool's own arguments.

    Small models write a git command line in the `command` field instead of filling `ref`, `path`
    and `limit`. Only the same four read-only subcommands are understood, and the result goes
    through the same checks as arguments given properly.
    """
    tokens = text.split()
    if tokens and tokens[0] == "git":
        tokens = tokens[1:]
    if not tokens or tokens[0] not in _SUBCOMMANDS:
        raise ToolError(f"Unknown git command {text!r}. Use status, diff, log or show.")
    command, rest = tokens[0], tokens[1:]
    refs: list[str] = []
    path = args.get("path")
    limit = args.get("limit")
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "--":
            if i + 1 < len(rest):
                path = rest[i + 1]
            break
        if token in _TYPED_FLAGS_IGNORED:
            pass
        elif command == "log" and re.fullmatch(r"-n?\d+", token):
            limit = int(token.lstrip("-n"))
        elif command == "log" and token == "-n" and i + 1 < len(rest) and rest[i + 1].isdigit():
            limit = int(rest[i + 1])
            i += 1
        elif token.startswith("-"):
            raise ToolError(
                f"The option {token!r} is not supported. git takes: command (status, diff, log, show), "
                "ref, path, limit. Example: command=diff, ref=HEAD~1, path=totals.py."
            )
        elif _is_existing_path(ws, token):
            path = token
        else:
            refs.append(token)
        i += 1
    ref = args.get("ref")
    if refs:
        if len(refs) == 1:
            ref = refs[0]
        elif command == "diff" and len(refs) == 2:
            ref = f"{refs[0]}..{refs[1]}"  # `git diff A B` and `git diff A..B` mean the same
        else:
            raise ToolError(f"git {command} takes one ref; got {', '.join(refs)}.")
    out = {**args, "command": command}
    for key, value in (("ref", ref), ("path", path), ("limit", limit)):
        if value is not None:
            out[key] = value
    return out


def _is_existing_path(ws: Workspace, token: str) -> bool:
    try:
        return ws.resolve(token).exists()
    except Exception:  # outside the project, or not a path
        return False


def make_git_tool(ws: Workspace) -> Tool:
    def check_ref(value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith("-") or not _REF.match(value):
            raise ToolError(f"Invalid ref {value!r}. Use a commit hash, branch or tag name, e.g. HEAD~1.")
        return value

    def git(args: Args) -> ToolResult:
        result = _git(args)
        path = args.get("path")
        if path and not ws.resolve(path).exists():
            # Often a guessed path like "src"; an empty answer alone would mislead the model.
            note = (
                f"\n\nNote: '{path}' does not exist in the project right now. If you guessed it, "
                "run this again without a path, or use list_files to see the real paths."
            )
            result = ToolResult(result.output + note, result.summary + f" · {path} doesn't exist", result.ok)
        return result

    def _git(args: Args) -> ToolResult:
        if " " in args["command"].strip():
            args = parse_typed_command(ws, args["command"].strip(), args)
        command = args["command"]
        ref = check_ref(args.get("ref"))
        paths = ["--", ws.rel(ws.resolve(args["path"]))] if args.get("path") else []

        if command == "status":
            proc = run_git(ws, ["status", "--short", "--branch", "--untracked-files=all", *paths])
            lines = proc.stdout.strip().splitlines()
            changed = [line for line in lines if not line.startswith("##")]
            new = sum(1 for line in changed if line.startswith("??"))
            out = proc.stdout.strip() or "(nothing)"
            out += (
                "\n\nCodes: M modified, A added to the index, D deleted, ?? untracked "
                "(new files git has never seen; `git diff` does not show them, read them with read_file)."
            )
            summary = f"{len(changed)} changed" + (f", {new} untracked" if new else "")
            return ToolResult(out, summary if changed else "clean")

        if command == "diff":
            base = ["diff", "--no-ext-diff", "--no-textconv", "--stat", "--patch"]
            proc = run_git(ws, [*base, *([ref] if ref else []), *paths])
            out = proc.stdout.strip() or "No differences. (Untracked files are not included; see status.)"
            files = sum(1 for line in out.splitlines() if line.startswith("diff --git"))
            return ToolResult(_clip(out), f"{files} files" if files else "no differences")

        if command == "log":
            limit = min(max(1, args.get("limit") or DEFAULT_LOG), MAX_LOG)
            proc = run_git(
                ws,
                ["log", "--no-ext-diff", "--no-textconv", f"-n{limit}", "--date=short",
                 "--format=%h %ad %an%n    %s", "--stat", *([ref] if ref else []), *paths],
            )
            out = proc.stdout.strip() or "No commits yet."
            commits = sum(1 for line in out.splitlines() if re.match(r"^[0-9a-f]{7,} ", line))
            return ToolResult(_clip(out), f"{commits} commits")

        if command == "show":
            proc = run_git(ws, ["show", "--no-ext-diff", "--no-textconv", "--stat", "--patch", ref or "HEAD", *paths])
            return ToolResult(_clip(proc.stdout.strip()), f"commit {ref or 'HEAD'}")

        raise ToolError(f"Unknown git command {command!r}. Use status, diff, log or show.")

    return Tool(
        "git",
        "Read-only git: see what changed in the project and when. "
        "status: uncommitted changes and untracked (brand-new) files. "
        "diff: uncommitted changes, or changes since a ref. "
        "log: recent commits. show: one commit. Use this to find a user's recent or latest changes.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": ["status", "diff", "log", "show"]},
                "path": {"type": "string", "description": "Limit to this file or directory."},
                "ref": {"type": "string", "description": "Commit, branch or tag (diff: compare against it; log: start from it; show: the commit). Default HEAD."},
                "limit": {"type": "integer", "description": f"log only: number of commits (default {DEFAULT_LOG})."},
            },
            "required": ["command"],
        },
        git,
    )
