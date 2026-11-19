"""Mining commit-replay tasks, on a synthetic repository (GitHub is replaced by a stub)."""

import os
import subprocess
import tomllib

import pytest
from typer.testing import CliRunner

import firox.usecases.commits as commits
from firox.cli import app
from firox.evals.task import load_task
from firox.evals.verify import verify

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()


def commit(repo, message, files):
    for path, content in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "remote", "add", "origin", "git@github.com:someone/shop.git")
    commit(repo, "Initial version", {
        "pyproject.toml": "[project]\nname = 'shop'\n",
        "cart.py": "def total(prices):\n    return sum(prices)\n",
        "tests/test_cart.py": "from cart import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n",
    })
    # a good candidate: changes code and adds the test that shows it
    commit(repo, "fix: apply the discount to the total\n\nTotals ignored the discount code.", {
        "cart.py": "def total(prices, discount=0):\n    return sum(prices) * (100 - discount) // 100\n",
        "tests/test_cart.py": ("from cart import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n\n\n"
                               "def test_discount():\n    assert total([100, 100], discount=10) == 180\n"),
    })
    commit(repo, "Update the readme", {"README.md": "# Shop\n"})  # no tests: not a candidate
    commit(repo, "Big rewrite", {f"m{i}.py": "x = 1\n" for i in range(5)} | {"tests/test_m.py": "def test(): pass\n"})
    return repo


@pytest.fixture
def public(monkeypatch):
    seen = []

    def fake(owner, name):
        seen.append(f"{owner}/{name}")
        return {"visibility": "public", "private": False, "license": {"spdx_id": "Apache-2.0"}}

    monkeypatch.setattr(commits, "github_repo", fake)
    return seen


def test_repo_info_needs_a_public_licensed_github_repository(repo, public, monkeypatch):
    info = commits.repo_info(repo)
    assert (info.url, info.license) == ("https://github.com/someone/shop", "Apache-2.0")
    assert public == ["someone/shop"]

    monkeypatch.setattr(commits, "github_repo", lambda o, n: {"visibility": "private", "private": True})
    with pytest.raises(commits.MinerError, match="not public"):
        commits.repo_info(repo)
    monkeypatch.setattr(commits, "github_repo", lambda o, n: {"visibility": "public", "license": None})
    with pytest.raises(commits.MinerError, match="no license"):
        commits.repo_info(repo)


def test_repositories_off_github_or_excluded_are_refused(repo, public, tmp_path, monkeypatch):
    git(repo, "remote", "set-url", "origin", "https://gitlab.example.com/x/shop.git")
    with pytest.raises(commits.MinerError, match="not a GitHub repository"):
        commits.repo_info(repo)
    exclude = tmp_path / "exclude.txt"
    exclude.write_text(f"{repo}\n")
    monkeypatch.setenv("FIROX_EXCLUDE", str(exclude))
    with pytest.raises(commits.MinerError, match="exclusion list"):
        commits.repo_info(repo)


def test_candidates_are_small_commits_with_code_and_tests(repo):
    [candidate] = commits.candidates(repo)
    assert candidate.subject == "fix: apply the discount to the total"
    assert candidate.tests == ["tests/test_cart.py"] and candidate.sources == ["cart.py"]
    assert candidate.category == "fix" and candidate.lines == 8
    assert commits.candidates(repo, category="docs") == []


def test_test_commands_per_build_tool(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    assert commits.test_command(tmp_path, ["core/src/test/java/a/FooTest.java", "src/test/java/BarTest.java"]) == (
        "mvn -o -q -pl core -am test -Dtest=BarTest,FooTest -Dsurefire.failIfNoSpecifiedTests=false")
    python = tmp_path / "py"
    python.mkdir()
    assert commits.test_command(python, ["tests/test_cart.py"]) == "python3 -m pytest -q tests/test_cart.py"


def test_the_draft_references_the_commit_and_verifies(repo, public, tmp_path):
    info = commits.repo_info(repo)
    [candidate] = commits.candidates(repo)
    text = commits.draft_toml(info, candidate)
    data = tomllib.loads(text)
    assert data["workspace"]["url"] == "https://github.com/someone/shop"
    assert data["workspace"]["commit"] == candidate.sha and data["workspace"]["license"] == "Apache-2.0"
    assert "Apply the discount to the total." in data["prompt"] and "Totals ignored the discount code." in data["prompt"]
    assert [c["type"] for c in data["checks"]] == ["command", "unchanged", "only_changed"]
    assert "def total" not in text  # the code itself is never copied

    task_dir = tmp_path / "tasks" / commits.task_id(info, candidate)
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(text)
    result = verify(load_task(task_dir))
    assert result.fails_as_given and result.passes_with_solution  # fails on the parent, passes on the commit


def test_the_workspace_can_come_from_the_url_alone(repo, public, tmp_path):
    info = commits.repo_info(repo)
    [candidate] = commits.candidates(repo)
    text = commits.draft_toml(info, candidate).replace(f'repo = "{commits._home_relative(repo)}"\n', "")
    text = text.replace('url = "https://github.com/someone/shop"', f'url = "{repo.as_uri()}"')  # stands in for GitHub
    task_dir = tmp_path / "tasks" / "from-url"
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(text)
    assert load_task(task_dir).workspace.repo is None
    assert verify(load_task(task_dir)).ok


def test_propose_lists_candidates_and_writes_nothing(repo, public, tmp_path):
    result = CliRunner().invoke(app, ["eval", "propose", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert "1 candidates" in result.output and "apply the discount" in result.output
    assert "Apache-2.0" in result.output


def test_add_writes_only_after_approval(repo, public, tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    sha = git(repo, "rev-parse", "HEAD~2")
    args = ["eval", "add", "--repo", str(repo), "--commit", sha[:7], "--tasks-dir", str(tasks)]
    declined = CliRunner().invoke(app, args, input="n\n")
    assert declined.exit_code == 1 and "fails on the parent commit" in declined.output and "Not written." in declined.output
    assert list(tasks.iterdir()) == []

    approved = CliRunner().invoke(app, args, input="y\n")
    assert approved.exit_code == 0, approved.output
    [task_dir] = tasks.iterdir()
    assert load_task(task_dir).source == "commit-replay"


def test_add_refuses_commits_that_cannot_be_replayed(repo, public, tmp_path):
    readme = git(repo, "rev-parse", "HEAD~1")
    result = CliRunner().invoke(app, ["eval", "add", "--repo", str(repo), "--commit", readme, "--tasks-dir", str(tmp_path)])
    assert result.exit_code == 1 and "can't be replayed" in result.output


def test_add_refuses_a_task_that_does_not_separate_work(repo, public, tmp_path):
    # a commit whose tests already pass on its parent: the task would pass without any work
    sha = commit(repo, "Add a test for totals", {"tests/test_more.py": "from cart import total\n\n\ndef test_sum():\n    assert total([1]) == 1\n",
                                                 "notes.py": "NOTE = 1\n"})
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    result = CliRunner().invoke(app, ["eval", "add", "--repo", str(repo), "--commit", sha, "--tasks-dir", str(tasks), "--yes"])
    assert result.exit_code == 1 and "doesn't separate failing from passing work" in result.output
    assert list(tasks.iterdir()) == []
