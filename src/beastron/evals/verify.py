"""Verify a task: its checks must fail as given and pass with the reference solution.

That rules out impossible tasks and tasks that pass without any work. The
reference solution of a replayed commit is the commit itself; of any other task
(synthetic, or a question about a repository at a commit), its `solution/`
folder: files to overlay, `answer.md` for answers, and `solution.sh` for solutions
that are commands (e.g. git operations), run in the workspace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from .checks import CheckResult, Context, evaluate, snapshot
from .task import Task
from .workspace import GIT_ENV, SETUP_TIMEOUT, apply_target, prepared


@dataclass
class Verification:
    fails_as_given: bool
    passes_with_solution: bool
    as_given: list[CheckResult]
    with_solution: list[CheckResult]

    @property
    def ok(self) -> bool:
        return self.fails_as_given and self.passes_with_solution


def verify(task: Task) -> Verification:
    # Two separate workspaces: build output from the first check (e.g. Maven's
    # target/) must not leak into the second, or a stale class could hide the solution.
    with prepared(task) as ws:
        as_given = evaluate(task.checks, Context(ws.root, "", snapshot(ws.root)))
    with prepared(task) as ws:
        before = snapshot(ws.root)
        answer = ""
        if task.source == "commit-replay" and task.workspace.at == "parent":
            apply_target(ws)  # the real commit is the reference solution
        else:
            solution = task.dir / "solution"
            for path in solution.rglob("*") if solution.is_dir() else []:
                if path.is_file() and path.name not in ("answer.md", "solution.sh"):
                    target = ws.root / path.relative_to(solution)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)  # a new modification time, like a real edit
            script = solution / "solution.sh"
            if script.is_file():  # solutions that are commands, e.g. git operations
                proc = subprocess.run(["sh", str(script)], cwd=ws.root, capture_output=True, text=True,
                                      timeout=SETUP_TIMEOUT, env={**os.environ, **GIT_ENV})
                if proc.returncode != 0:
                    raise RuntimeError(f"solution.sh of {task.id} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
            answer_file = solution / "answer.md"
            answer = answer_file.read_text(encoding="utf-8") if answer_file.is_file() else ""
        with_solution = evaluate(task.checks, Context(ws.root, answer, before))
    return Verification(
        fails_as_given=not all(r.passed for r in as_given),
        passes_with_solution=all(r.passed for r in with_solution),
        as_given=as_given,
        with_solution=with_solution,
    )
