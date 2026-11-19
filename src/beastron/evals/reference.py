"""Claude Code as the reference: the same tasks, the same workspaces, the same checks.

beastron's target is a share of what Claude Code completes with a pinned model
(see docs/plan.md § 2). Each run:
1. creates the task's fresh workspace (beastron.evals.workspace) and snapshots it
2. runs `claude -p <prompt> --model <model>` in it, headless, with:
   - `--safe-mode`: no CLAUDE.md, skills, plugins, hooks or MCP servers, so the
     owner's own setup doesn't change the result (auth and built-in tools work)
   - `--no-session-persistence`: no session files under ~/.claude, which would
     otherwise show up as real usage in `beastron usecases`
   - `--dangerously-skip-permissions`: everything is approved, as in beastron's
     evals; the whole process runs in the same write sandbox (beastron.evals.sandbox),
     which also allows Claude Code's own state (~/.claude, ~/.claude.json, its caches)
   - `--output-format stream-json --verbose`: one JSON event per line
3. enforces the task's time limit; Claude Code has no step limit option, so a
   task's `max_steps` doesn't apply here
4. evaluates the task's checks, with Claude Code's final message as the answer
5. appends one result line to the run's results.jsonl, like a beastron run

The event stream is kept as the run's trace under the state folder (it holds
the whole transcript, so it never goes into git); the committed summary has
counts only. The model ID Claude Code reports and its version are recorded per
run, so the report can flag a run made with a different Claude Code version.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..trace import beastron_version
from . import sandbox
from .checks import Context, evaluate, snapshot
from .report import load_results
from .runner import RunResult, _append, _write_json, new_run_dir
from .task import Task
from .workspace import WorkspaceError, prepared

DEFAULT_MODEL = "claude-sonnet-5-5"
FLAGS = (
    "--output-format", "stream-json", "--verbose",
    "--safe-mode", "--no-session-persistence", "--dangerously-skip-permissions",
)
# Set by a Claude Code session for its own child processes; a nested run must not inherit them.
PARENT_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
STDERR_TAIL = 600
LIMIT_MARKERS = ("hit your", "usage limit", "rate limit")  # in Claude Code's message when the account's limit is reached


class LimitReached(Exception):
    """The account's usage limit stopped Claude Code: the run says nothing about the task, so it isn't recorded."""


def profile_name(model: str) -> str:
    """The name reference results are stored under, e.g. claude-code-sonnet-5-5."""
    return "claude-code-" + model.removeprefix("claude-")


def claude_version(claude: str = "claude") -> str | None:
    """Claude Code's version, e.g. "2.1.328"; None if it can't be found."""
    try:
        proc = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = proc.stdout.split()
    return words[0] if proc.returncode == 0 and words else None


def sandbox_prefix(workspace_parent: Path) -> list[str]:
    """The eval sandbox, plus the places Claude Code itself writes to."""
    home = Path.home()
    writable = [workspace_parent, home / ".claude", home / "Library" / "Caches"]
    # ~/.claude.json is replaced atomically: a temporary file next to it, then a rename
    regexes = ("^" + _regex_escape(str(home.resolve() / ".claude.json")),)
    return sandbox.command_prefix(writable, regexes)


def command(prompt: str, model: str, claude: str = "claude") -> list[str]:
    return [claude, "-p", prompt, "--model", model, *FLAGS]


def run_reference_task(
    task: Task,
    *,
    model: str = DEFAULT_MODEL,
    claude: str = "claude",
    version: str | None = None,
    repeat: int = 1,
    run_dir: Path,
    use_sandbox: bool = True,
    keep: bool = False,
) -> RunResult:
    """Run one task once through Claude Code and append its result to `run_dir/results.jsonl`."""
    started = time.monotonic()
    result = RunResult(
        task=task.id, category=task.category, profile=profile_name(model), model=model,
        repeat=repeat, passed=False, reason="setup_failed", sandboxed=use_sandbox,
        beastron_commit=beastron_version().get("commit"), claude_code_version=version,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        with prepared(task, keep=keep) as workspace:
            root = workspace.root
            prefix = sandbox_prefix(root.parent) if use_sandbox else []
            before = snapshot(root)
            trace = run_dir / "traces" / f"{task.id}-{result.profile}-{repeat}-{uuid.uuid4().hex[:6]}.jsonl"
            result.trace = str(trace)
            exit_code, timed_out, stderr = _run_claude(prefix + command(task.prompt, model, claude), root, trace, task.timeout)
            stream = read_stream(trace)
            _fill(result, stream, exit_code=exit_code, timed_out=timed_out, stderr=stderr)

            outcomes = evaluate(task.checks, Context(root, stream.answer, before))
            result.checks = [{"type": o.check.type, "passed": o.passed, "reason": o.reason} for o in outcomes]
            result.passed = result.reason not in ("timeout", "error") and all(o.passed for o in outcomes)
    except (WorkspaceError, sandbox.SandboxError, OSError) as e:
        result.error = str(e)
    result.seconds = round(time.monotonic() - started, 2)
    if limit_hit(result):
        raise LimitReached(result.error)
    _append(run_dir / "results.jsonl", result)
    return result


def limit_hit(result: RunResult) -> bool:
    return result.reason == "error" and any(m in (result.error or "").lower() for m in LIMIT_MARKERS)


def run_reference_suite(
    tasks: list[Task],
    *,
    model: str = DEFAULT_MODEL,
    claude: str = "claude",
    repeats: int = 3,
    run_dir: Path | None = None,
    progress: Callable[[RunResult], None] | None = None,
    **kwargs: Any,
) -> tuple[Path, list[RunResult]]:
    """Run every task `repeats` times. With an existing `run_dir`, runs already recorded there are skipped,
    so a run stopped by the usage limit (LimitReached) can be continued."""
    run_dir = run_dir or new_run_dir()
    (run_dir / "traces").mkdir(parents=True, exist_ok=True)
    version = claude_version(claude)
    done = {(r["task"], r["repeat"]) for r in load_results(run_dir)} if (run_dir / "results.jsonl").is_file() else set()
    _write_json(run_dir / ("run.json" if not done else f"run-resumed-{int(time.time())}.json"), {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tasks": [t.id for t in tasks],
        "profiles": [profile_name(model)],
        "repeats": repeats,
        "beastron": beastron_version(),
        "reference": {"model": model, "claude_code_version": version, "flags": list(FLAGS)},
        "sandboxed": kwargs.get("use_sandbox", True),
    })
    results = []
    for task in tasks:
        for repeat in range(1, repeats + 1):
            if (task.id, repeat) in done:
                continue
            result = run_reference_task(task, model=model, claude=claude, version=version, repeat=repeat,
                                        run_dir=run_dir, **kwargs)
            results.append(result)
            if progress:
                progress(result)
    return run_dir, results


# ----- the event stream --------------------------------------------------------------

class Stream:
    """What a run's stream-json output says, read back from its trace file."""

    def __init__(self) -> None:
        self.init: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None
        self.tool_calls = self.tool_errors = self.shell_commands = 0
        self.bad_lines = 0

    @property
    def answer(self) -> str:
        return (self.result or {}).get("result") or ""


def read_stream(path: Path) -> Stream:
    stream = Stream()
    if not path.is_file():
        return stream
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            stream.bad_lines += 1
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        content = (event.get("message") or {}).get("content")
        if kind == "system" and event.get("subtype") == "init":
            stream.init = event
        elif kind == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    stream.tool_calls += 1
                    stream.shell_commands += block.get("name") == "Bash"
        elif kind == "user" and isinstance(content, list):
            stream.tool_errors += sum(
                1 for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")
            )
        elif kind == "result":
            stream.result = event  # the last one wins
    return stream


def _fill(result: RunResult, stream: Stream, *, exit_code: int | None, timed_out: bool, stderr: str) -> None:
    final = stream.result
    if stream.init.get("claude_code_version"):
        result.claude_code_version = stream.init["claude_code_version"]
    result.tool_calls, result.tool_errors, result.shell_commands = stream.tool_calls, stream.tool_errors, stream.shell_commands
    errors = []
    if final:
        usage = final.get("usage") or {}
        result.steps = int(final.get("num_turns") or 0)
        result.prompt_tokens = sum(int(usage.get(k) or 0) for k in
                                   ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        result.completion_tokens = int(usage.get("output_tokens") or 0)
        result.model_seconds = round((final.get("duration_api_ms") or 0) / 1000, 2)
        result.models_used = sorted(final.get("modelUsage") or {})
        result.cost_usd = final.get("total_cost_usd")
        if final.get("is_error"):
            errors.append(f"Claude Code: {final.get('subtype')}: {str(final.get('result') or '')[:STDERR_TAIL]}")
    if timed_out:
        result.reason = "timeout"
    elif final is None or (final.get("is_error") and final.get("subtype") != "error_max_turns"):
        result.reason = "error"
    elif final.get("subtype") == "error_max_turns":
        result.reason = "max_steps"
    else:
        result.reason = "done"
    if final is None and not timed_out:
        errors.append(f"no result from Claude Code (exit code {exit_code})")
    if exit_code not in (0, None) and stderr.strip():
        errors.append(stderr.strip()[-STDERR_TAIL:])
    result.error = "; ".join(errors) or None


def _run_claude(cmd: list[str], cwd: Path, trace: Path, timeout: int) -> tuple[int | None, bool, str]:
    """Run Claude Code with its output going to the trace file. (exit code, timed out, stderr)."""
    env = {k: v for k, v in os.environ.items() if k not in PARENT_ENV}
    with trace.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                text=True, env=env, start_new_session=True)
        try:
            _, stderr = proc.communicate(timeout=timeout)
            return proc.returncode, False, stderr or ""
        except subprocess.TimeoutExpired:
            # its own group: Claude Code's shell commands and subagents go too
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _, stderr = proc.communicate()
            return None, True, stderr or ""


def _regex_escape(text: str) -> str:
    return "".join("\\" + ch if ch in r".^$*+?()[]{}|\\" else ch for ch in text)
