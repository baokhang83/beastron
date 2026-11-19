"""Verify a task: its checks must fail as given and pass with the reference solution.

That rules out impossible tasks and tasks that pass without any work. The
reference solution of a commit-replay task is the real commit; of a synthetic
task, its `solution/` folder (files to overlay, plus `answer.md` for answers).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .checks import CheckResult, Context, evaluate, snapshot
from .task import Task
from .workspace import apply_target, prepared


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
    with prepared(task) as ws:
        before = snapshot(ws.root)
        as_given = evaluate(task.checks, Context(ws.root, "", before))
        answer = ""
        if task.source == "commit-replay":
            apply_target(ws)
        else:
            solution = task.dir / "solution"
            for path in solution.rglob("*") if solution.is_dir() else []:
                if path.is_file() and path.name != "answer.md":
                    target = ws.root / path.relative_to(solution)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
            answer_file = solution / "answer.md"
            answer = answer_file.read_text(encoding="utf-8") if answer_file.is_file() else ""
        with_solution = evaluate(task.checks, Context(ws.root, answer, before))
    return Verification(
        fails_as_given=not all(r.passed for r in as_given),
        passes_with_solution=all(r.passed for r in with_solution),
        as_given=as_given,
        with_solution=with_solution,
    )
