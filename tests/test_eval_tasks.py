import os
import subprocess
import textwrap

import pytest

from firox.evals.checks import Check
from firox.evals.task import TaskError, load_task, load_tasks
from firox.evals.workspace import WorkspaceError, prepared

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}
CHECK = '[[checks]]\ntype = "command"\nrun = "true"\n'


def make_task(root, name, toml, fixture=True, files=None):
    task_dir = root / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(textwrap.dedent(toml))
    if fixture:
        (task_dir / "repo").mkdir()
        (task_dir / "repo" / "app.py").write_text("print('hi')\n")
    for path, content in (files or {}).items():
        (task_dir / path).write_text(content)
    return task_dir


SYNTHETIC = '''
prompt = "Fix the bug."
category = "fix"
language = "python"
source = "synthetic"
tags = ["seed", "quick"]
''' + CHECK


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()


# ----- loading -------------------------------------------------------------

def test_load_a_synthetic_task(tmp_path):
    task = load_task(make_task(tmp_path, "fix-bug", SYNTHETIC))
    assert (task.id, task.category, task.source, task.tags) == ("fix-bug", "fix", "synthetic", ("seed", "quick"))
    assert task.workspace.fixture == "repo" and task.timeout == 600 and task.max_steps == 25
    assert task.checks == [Check(type="command", run="true")]


@pytest.mark.parametrize("toml, message", [
    ('category = "fix"\nlanguage = "py"\nsource = "synthetic"\n' + CHECK, "'prompt' is required"),
    (SYNTHETIC.replace('category = "fix"', 'category = "magic"'), "category 'magic' is not one of"),
    (SYNTHETIC.replace('source = "synthetic"', 'source = "dream"'), "source 'dream' is not one of"),
    # top-level keys must come before [[checks]]: in TOML, later lines belong to the check
    (SYNTHETIC.replace(CHECK, 'colour = "blue"\n' + CHECK), "unknown keys ['colour']"),
    (SYNTHETIC.replace(CHECK, ""), "at least one [[checks]]"),
    (SYNTHETIC.replace(CHECK, "timeout = -5\n" + CHECK), "'timeout' must be a positive"),
    (SYNTHETIC.replace(CHECK, '[[checks]]\nrun = "true"\n'), "check 1 needs a 'type'"),
    ("prompt = [", "invalid TOML"),
])
def test_invalid_tasks_name_the_file_and_the_problem(tmp_path, toml, message):
    task_dir = make_task(tmp_path, "bad", toml)
    with pytest.raises(TaskError, match=message.replace("[", r"\[").replace("]", r"\]")) as err:
        load_task(task_dir)
    assert str(task_dir / "task.toml") in str(err.value)


def test_missing_fixture_and_setup_are_reported(tmp_path):
    with pytest.raises(TaskError, match="fixture folder 'repo' not found"):
        load_task(make_task(tmp_path, "nofixture", SYNTHETIC, fixture=False))
    toml = SYNTHETIC + '[workspace]\nsetup = "setup.sh"\n'
    with pytest.raises(TaskError, match="setup script 'setup.sh' not found"):
        load_task(make_task(tmp_path, "nosetup", toml))


def test_commit_replay_needs_repo_and_commit(tmp_path):
    toml = SYNTHETIC.replace('source = "synthetic"', 'source = "commit-replay"') + '[workspace]\nrepo = "/x"\n'
    with pytest.raises(TaskError, match="needs \\[workspace\\] commit, and url or repo"):
        load_task(make_task(tmp_path, "replay", toml, fixture=False))


def test_load_tasks_by_tag_and_id(tmp_path):
    make_task(tmp_path, "a", SYNTHETIC)
    make_task(tmp_path, "b", SYNTHETIC.replace('tags = ["seed", "quick"]', 'tags = ["full"]'))
    assert [t.id for t in load_tasks(tmp_path)] == ["a", "b"]
    assert [t.id for t in load_tasks(tmp_path, tag="quick")] == ["a"]
    assert [t.id for t in load_tasks(tmp_path, ids=["b"])] == ["b"]
    with pytest.raises(TaskError, match="unknown task ids"):
        load_tasks(tmp_path, ids=["zzz"])


# ----- workspaces ------------------------------------------------------------

def test_synthetic_workspace_is_a_fresh_copy_and_is_cleaned_up(tmp_path):
    task = load_task(make_task(tmp_path / "tasks", "fix-bug", SYNTHETIC))
    with prepared(task) as ws:
        assert (ws.root / "app.py").read_text() == "print('hi')\n"
        (ws.root / "app.py").write_text("changed")  # a run's changes ...
        root = ws.root
    assert not root.exists()  # ... disappear with the workspace
    assert (task.dir / "repo" / "app.py").read_text() == "print('hi')\n"  # the fixture is untouched


def test_setup_script_builds_a_git_history(tmp_path):
    setup = "git init -q -b main && git add -A && git commit -q -m 'Initial version'\n"
    toml = SYNTHETIC + '[workspace]\nsetup = "setup.sh"\n'
    task = load_task(make_task(tmp_path / "tasks", "history", toml, files={"setup.sh": setup}))
    with prepared(task) as one, prepared(task) as two:
        assert git(one.root, "log", "--format=%s") == "Initial version"
        # fixed identities and dates: the same history, down to the commit id, every time
        assert git(one.root, "rev-parse", "HEAD") == git(two.root, "rev-parse", "HEAD")


def test_failing_setup_is_reported(tmp_path):
    toml = SYNTHETIC + '[workspace]\nsetup = "setup.sh"\n'
    task = load_task(make_task(tmp_path / "tasks", "broken", toml, files={"setup.sh": "echo boom >&2; exit 3\n"}))
    with pytest.raises(WorkspaceError, match="setup script of task broken failed: boom"):
        with prepared(task):
            pass


@pytest.fixture
def source_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Add calc")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "commit", "-q", "-am", "Fix add")
    (repo / "scratch.txt").write_text("uncommitted local work")  # must not leak into the workspace
    return repo


def replay_task(tmp_path, repo, commit):
    toml = SYNTHETIC.replace('source = "synthetic"', 'source = "commit-replay"') + (
        f'[workspace]\nrepo = "{repo}"\ncommit = "{commit}"\n'
    )
    return load_task(make_task(tmp_path / "tasks", "replay", toml, fixture=False))


def test_commit_replay_workspace_is_exactly_the_parent_commit(tmp_path, source_repo):
    fix = git(source_repo, "rev-parse", "HEAD")
    parent = git(source_repo, "rev-parse", "HEAD^")
    task = replay_task(tmp_path, source_repo, fix[:7])
    with prepared(task) as ws:
        assert (ws.base_commit, ws.target_commit) == (parent, fix)
        assert git(ws.root, "rev-parse", "HEAD") == parent
        assert git(ws.root, "status", "--porcelain", "--untracked-files=all") == ""  # nothing extra
        assert "return a - b" in (ws.root / "calc.py").read_text()  # the state before the fix
        assert not (ws.root / "scratch.txt").exists()
    assert git(source_repo, "rev-parse", "HEAD") == fix  # the source repository is untouched


def test_commit_replay_refuses_excluded_repositories(tmp_path, source_repo, monkeypatch):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text(f"# never use this one\n{source_repo}\n")
    monkeypatch.setenv("FIROX_EXCLUDE", str(exclude))
    task = replay_task(tmp_path, source_repo, "HEAD")
    with pytest.raises(WorkspaceError, match="exclusion list"):
        with prepared(task):
            pass


def test_commit_replay_of_a_root_commit_fails_clearly(tmp_path, source_repo):
    root_commit = git(source_repo, "rev-list", "--max-parents=0", "HEAD")
    with pytest.raises(WorkspaceError, match="rev-parse failed"):
        with prepared(replay_task(tmp_path, source_repo, root_commit)):
            pass
