"""Run eval tasks headless and record one result per run.

For each task x model profile x repeat:
1. create a fresh workspace (beastron.evals.workspace) and snapshot its files
2. run the agent on the task's prompt, approving every action automatically,
   with every shell command inside the sandbox (beastron.evals.sandbox)
3. enforce the task's time limit (wall clock) and step limit
4. evaluate the task's checks (beastron.evals.checks)
5. append one JSON line to the run's results file, even when the model errors

Results and session traces go to `<state>/evals/runs/<run id>/`:
`results.jsonl` (one line per run) and `traces/` (one session trace per run).
"""

from __future__ import annotations

import json
import os
import signal
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agent import Agent
from ..client import ModelClient, OpenAICompatClient
from ..config import ModelProfile
from ..events import (DiscardAttempt, ErrorEvent, Event, Nudge, NumericTestClaims,
                      StepObservation, ToolEnd, ToolObservation, TurnEnd)
from ..tools import Workspace, make_tools
from ..trace import SessionTrace, fan_out, beastron_version, model_digest, state_dir
from . import sandbox
from .checks import Context, evaluate, snapshot
from .task import Task
from .workspace import WorkspaceError, prepared

ClientFactory = Callable[[ModelProfile], ModelClient]


@dataclass
class RunResult:
    task: str
    category: str
    profile: str
    model: str
    repeat: int
    passed: bool
    reason: str  # how the turn ended: done, max_steps, interrupted, error, timeout, setup_failed
    checks: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    model_seconds: float = 0.0
    nudges: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    git_discard_blocked: int = 0
    git_discard_allowed: int = 0
    tool_seconds: dict[str, float] = field(default_factory=dict)
    step_usage: list[dict[str, Any]] = field(default_factory=list)
    short_reads: int = 0
    overlapping_reads: int = 0
    whole_file_rewrites: int = 0
    unsupported_numeric_test_claims: int = 0
    changes: list[dict[str, Any]] = field(default_factory=list)
    shell_commands: int = 0
    error: str | None = None
    model_digest: str | None = None
    beastron_commit: str | None = None
    # reference runs (beastron.evals.reference) only
    claude_code_version: str | None = None
    models_used: list[str] = field(default_factory=list)  # every model Claude Code called, from its own report
    cost_usd: float | None = None  # Claude Code's estimate at list prices, not what a subscription is charged
    sandboxed: bool = True
    trace: str | None = None
    started_at: str = ""


class _Collector:
    """Counts what the report needs from the agent's events."""

    def __init__(self) -> None:
        self.nudges = self.tool_calls = self.tool_errors = 0
        self.git_discard_blocked = self.git_discard_allowed = 0
        self.tool_seconds: dict[str, float] = {}
        self.step_usage: list[dict[str, Any]] = []
        self.short_reads = self.overlapping_reads = self.whole_file_rewrites = 0
        self.unsupported_numeric_test_claims = 0
        self.turn_end: TurnEnd | None = None
        self.errors: list[str] = []

    def __call__(self, event: Event) -> None:
        match event:
            case StepObservation():
                self.step_usage.append(asdict(event))
            case ToolObservation(name=name, elapsed=elapsed):
                self.tool_seconds[name] = self.tool_seconds.get(name, 0.0) + elapsed
                self.short_reads += event.short_read
                self.overlapping_reads += event.overlapping_read
                self.whole_file_rewrites += event.whole_file_rewrite
            case NumericTestClaims(unsupported_count=count):
                self.unsupported_numeric_test_claims += count
            case DiscardAttempt(blocked=blocked):
                if blocked:
                    self.git_discard_blocked += 1
                else:
                    self.git_discard_allowed += 1
            case Nudge():
                self.nudges += 1
            case ToolEnd(ok=ok):
                self.tool_calls += 1
                self.tool_errors += 0 if ok else 1
            case ErrorEvent(message=message):
                self.errors.append(message)
            case TurnEnd():
                self.turn_end = event


@contextmanager
def _time_limit(seconds: int) -> Iterator[dict[str, bool]]:
    """Interrupt the run after `seconds` of wall-clock time.

    The timer raises KeyboardInterrupt, which the agent already handles cleanly
    (it ends the turn and repairs its history); a shell command that is
    running gets killed by subprocess on the way out. Needs the main thread.
    """
    state = {"fired": False}

    def fire(signum, frame):  # noqa: ARG001
        state["fired"] = True
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield state
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def new_run_dir() -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    path = state_dir() / "evals" / "runs" / run_id
    (path / "traces").mkdir(parents=True)
    return path


def run_task(
    task: Task,
    profile: ModelProfile,
    *,
    repeat: int = 1,
    run_dir: Path,
    client_factory: ClientFactory = OpenAICompatClient,
    use_sandbox: bool = True,
    keep: bool = False,
    on_event: Callable[[Event], None] | None = None,
) -> RunResult:
    """Run one task once and append its result to `run_dir/results.jsonl`."""
    started = time.monotonic()
    result = RunResult(
        task=task.id, category=task.category, profile=profile.name, model=profile.model,
        repeat=repeat, passed=False, reason="setup_failed", sandboxed=use_sandbox,
        model_digest=model_digest(profile), beastron_commit=beastron_version().get("commit"),
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        with prepared(task, keep=keep) as workspace:
            ws = Workspace(workspace.root)
            prefix = sandbox.command_prefix([workspace.root.parent]) if use_sandbox else None
            before = snapshot(ws.root)
            trace = SessionTrace.start(ws, profile, directory=run_dir / "traces")
            result.trace = str(trace.path)
            collector = _Collector()
            agent = Agent(
                client_factory(profile), ws, make_tools(ws, shell_prefix=prefix),
                on_event=fan_out(collector, trace, on_event),
                approve=lambda request: True,  # evals approve everything; shell commands are sandboxed
                max_steps=task.max_steps if task.max_steps_explicit or task.max_steps != 25 else profile.max_steps,
            )
            with _time_limit(task.timeout) as timer:
                try:
                    agent.run(task.prompt)
                except KeyboardInterrupt:
                    # the timer can fire just before the agent starts handling interrupts
                    if not timer["fired"]:
                        raise
                except Exception as e:  # a bug must not lose the result
                    collector.errors.append(f"{type(e).__name__}: {e}")
            trace.close()

            end = collector.turn_end
            result.reason = "timeout" if timer["fired"] else (end.reason if end else "error")
            if end:
                result.steps, result.prompt_tokens, result.completion_tokens = end.steps, end.prompt_tokens, end.completion_tokens
                result.model_seconds = round(end.model_seconds, 2)
                result.changes = [asdict(c) for c in end.changes]
                result.shell_commands = end.shell_commands
            result.nudges, result.tool_calls, result.tool_errors = collector.nudges, collector.tool_calls, collector.tool_errors
            result.git_discard_blocked = collector.git_discard_blocked
            result.git_discard_allowed = collector.git_discard_allowed
            result.tool_seconds = {name: round(seconds, 3) for name, seconds in collector.tool_seconds.items()}
            result.step_usage = collector.step_usage
            result.short_reads = collector.short_reads
            result.overlapping_reads = collector.overlapping_reads
            result.whole_file_rewrites = collector.whole_file_rewrites
            result.unsupported_numeric_test_claims = collector.unsupported_numeric_test_claims
            result.error = "; ".join(collector.errors) or None

            outcomes = evaluate(task.checks, Context(ws.root, _final_answer(agent), before))
            result.checks = [
                {"type": o.check.type, "passed": o.passed, "reason": o.reason} for o in outcomes
            ]
            result.passed = result.reason not in ("timeout", "error") and all(o.passed for o in outcomes)
    except (WorkspaceError, sandbox.SandboxError) as e:
        result.error = str(e)
    result.seconds = round(time.monotonic() - started, 2)
    _append(run_dir / "results.jsonl", result)
    if result.reason == "interrupted":
        raise KeyboardInterrupt  # Ctrl-C by the user (not the timer): stop the suite, the result is saved
    return result


def run_suite(
    tasks: list[Task],
    profiles: list[ModelProfile],
    *,
    repeats: int = 3,
    run_dir: Path | None = None,
    progress: Callable[[RunResult], None] | None = None,
    **kwargs: Any,
) -> tuple[Path, list[RunResult]]:
    run_dir = run_dir or new_run_dir()
    (run_dir / "traces").mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "run.json", {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tasks": [t.id for t in tasks],
        "profiles": [p.name for p in profiles],
        "repeats": repeats,
        "beastron": beastron_version(),
        "sandboxed": kwargs.get("use_sandbox", True),
    })
    results = []
    for profile in profiles:
        for task in tasks:
            for repeat in range(1, repeats + 1):
                result = run_task(task, profile, repeat=repeat, run_dir=run_dir, **kwargs)
                results.append(result)
                if progress:
                    progress(result)
    return run_dir, results


def _final_answer(agent: Agent) -> str:
    for message in reversed(agent.messages):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            return message.get("content") or ""
    return ""


def _append(path: Path, result: RunResult) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
