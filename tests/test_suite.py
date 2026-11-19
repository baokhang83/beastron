"""The eval suite is well-formed.

Every task must fail as given and pass with its reference solution (that rules
out impossible tasks and tasks that pass without any work). Synthetic tasks are
verified here; tasks built on the owner's repositories need those local clones
and the Maven cache, so `beastron eval verify` checks them on the reference
machine, and here they only get structural checks (public commit, license, no
copied code).
"""

import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from beastron.evals.task import CATEGORIES, load_tasks
from beastron.evals.verify import verify

TASKS = Path(__file__).resolve().parents[1] / "evals" / "tasks"
ALL = load_tasks(TASKS)
SYNTHETIC = [t for t in ALL if t.source == "synthetic"]
REPO_BASED = [t for t in ALL if t.source == "commit-replay"]

# The versions the Maven tasks pin; `mvn -o` only works where all of them are cached.
PINNED_MAVEN = [
    "org/apache/maven/plugins/maven-compiler-plugin/3.13.0",
    "org/apache/maven/plugins/maven-surefire-plugin/3.5.2",
    "org/apache/maven/plugins/maven-resources-plugin/3.3.1",
    "org/junit/jupiter/junit-jupiter/5.11.4",
    "org/junit/jupiter/junit-jupiter-params/5.11.4",
]
COMPATIBLE_LICENSES = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0"}


def needs_maven_cache(task):
    uses_mvn = any(c.type == "command" and "mvn " in (c.run or "") for c in task.checks)
    repository = Path.home() / ".m2" / "repository"
    cached = all((repository / path).is_dir() for path in PINNED_MAVEN)
    return uses_mvn and (shutil.which("mvn") is None or not cached)


def test_the_suite_covers_every_category():
    counts = Counter(t.category for t in ALL)
    assert len(ALL) >= 30
    assert all(counts[c] >= 2 for c in CATEGORIES), counts
    assert len([t for t in ALL if "seed" in t.tags]) == 8
    quick = [t for t in ALL if "quick" in t.tags]
    assert 8 <= len(quick) <= 14
    assert any(t.language == "java" for t in quick)  # most of the owner's work is Java


@pytest.mark.parametrize("task", SYNTHETIC, ids=lambda t: t.id)
def test_synthetic_tasks_fail_as_given_and_pass_with_their_solution(task):
    if needs_maven_cache(task):
        pytest.skip("needs mvn with the pinned versions in ~/.m2")
    result = verify(task)
    assert result.fails_as_given, "the task would pass without any work"
    assert result.passes_with_solution, [r.reason for r in result.with_solution if not r.passed]


@pytest.mark.parametrize("task", REPO_BASED, ids=lambda t: t.id)
def test_repository_tasks_only_reference_public_commits(task):
    ws = task.workspace
    assert re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", ws.url or ""), ws.url
    assert re.fullmatch(r"[0-9a-f]{40}", ws.commit), "pin the full commit hash"
    assert ws.license in COMPATIBLE_LICENSES, ws.license
    # nothing from the repository is copied here: only task.toml, plus the reference answer for questions
    files = {p.relative_to(task.dir).as_posix() for p in task.dir.rglob("*") if p.is_file()}
    expected = {"task.toml"} | ({"solution/answer.md"} if ws.at == "commit" else set())
    assert files == expected, files
    if ws.at == "parent":
        assert ws.tests, "a replay needs the commit's tests to make pass"
