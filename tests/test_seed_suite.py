"""The seed suite is well-formed: every task fails as given, and passes with its reference solution.

That rules out impossible tasks and tasks that pass without any work. Each task's
`solution/` folder holds the files a correct solution changes, overlaid on the
workspace, and `answer.md` for tasks whose result is an answer.
"""

import shutil
from pathlib import Path

import pytest

from firox.evals.checks import Context, evaluate, snapshot
from firox.evals.task import load_tasks
from firox.evals.workspace import prepared

TASKS = Path(__file__).resolve().parents[1] / "evals" / "tasks"
SEED = load_tasks(TASKS, tag="seed")


def needs_missing_tool(task):
    """Maven tasks can only be checked where mvn and the offline cache exist (not in CI)."""
    uses_mvn = any(c.type == "command" and c.run.startswith("mvn") for c in task.checks)
    return uses_mvn and (shutil.which("mvn") is None or not (Path.home() / ".m2" / "repository").is_dir())


def test_the_seed_suite_has_eight_tasks_across_categories():
    assert len(SEED) == 8
    assert {t.category for t in SEED} >= {"fix", "explain", "git", "feature", "config"}
    assert {t.language for t in SEED} >= {"python", "java"}
    quick = [t.id for t in SEED if "quick" in t.tags]
    assert 4 <= len(quick) <= 6


@pytest.mark.parametrize("task", SEED, ids=lambda t: t.id)
def test_checks_fail_on_the_original_project(task):
    with prepared(task) as ws:
        results = evaluate(task.checks, Context(ws.root, "", snapshot(ws.root)))
    assert not all(r.passed for r in results), "the task would pass without any work"


@pytest.mark.parametrize("task", SEED, ids=lambda t: t.id)
def test_checks_pass_with_the_reference_solution(task):
    if needs_missing_tool(task):
        pytest.skip("needs mvn with a local ~/.m2 cache")
    solution = task.dir / "solution"
    assert solution.is_dir(), f"{task.id} has no solution/ folder"
    with prepared(task) as ws:
        before = snapshot(ws.root)
        for path in solution.rglob("*"):
            if path.is_file() and path.name != "answer.md":
                target = ws.root / path.relative_to(solution)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        answer_file = solution / "answer.md"
        answer = answer_file.read_text() if answer_file.is_file() else ""
        results = evaluate(task.checks, Context(ws.root, answer, before))
    failed = [r.reason for r in results if not r.passed]
    assert not failed, failed
