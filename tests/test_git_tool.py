import os
import stat
import subprocess

import pytest

from beastron.tools import ToolError, Workspace, make_tools
from beastron.tools.git import is_tracked

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=GIT_ENV)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-q", "-m", "Add the add function")
    # uncommitted work: one modified file, one brand-new file
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b + 0\n")
    (tmp_path / "x_client.py").write_text("print('fetch users')\n")
    return tmp_path


@pytest.fixture
def tool(repo):
    return {t.name: t for t in make_tools(Workspace(repo))}["git"]


def run(tool, **args):
    return tool.run(tool.check_args(args))


def test_status_shows_modified_and_untracked(tool):
    result = run(tool, command="status")
    assert " M app.py" in result.output
    assert "?? x_client.py" in result.output
    assert result.summary == "2 changed, 1 untracked"


def test_diff_shows_changes_but_not_untracked(tool):
    result = run(tool, command="diff")
    assert "+    return a + b + 0" in result.output
    assert "x_client.py" not in result.output


def test_log_and_show(tool):
    assert "Add the add function" in run(tool, command="log").output
    shown = run(tool, command="show", ref="HEAD")
    assert "+def add(a, b):" in shown.output


def test_missing_path_is_pointed_out(tool):
    result = run(tool, command="log", path="src")
    assert "'src' does not exist" in result.output
    assert "doesn't exist" in result.summary


def test_path_must_stay_inside_the_project(tool):
    with pytest.raises(ToolError, match="outside the project"):
        run(tool, command="diff", path="../elsewhere")


def test_refs_cannot_inject_options(tool, repo):
    target = repo / "written-by-git.txt"
    with pytest.raises(ToolError, match="Invalid ref"):
        run(tool, command="diff", ref=f"--output={target}")
    assert not target.exists()


def test_not_a_repository(tmp_path):
    tool = {t.name: t for t in make_tools(Workspace(tmp_path))}["git"]
    with pytest.raises(ToolError, match="not a git repository"):
        run(tool, command="status")


def _plant_program(repo, name):
    marker = repo.parent / f"{repo.name}-{name}-ran"
    script = repo.parent / f"{repo.name}-{name}.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, marker


def test_repository_config_cannot_run_an_external_diff_program(tool, repo):
    script, marker = _plant_program(repo, "extdiff")
    git(repo, "config", "diff.external", str(script))
    git(repo, "diff")  # plain git does run it ...
    assert marker.exists()
    marker.unlink()
    run(tool, command="diff")  # ... beastron's git tool must not
    run(tool, command="show", ref="HEAD")
    run(tool, command="log")
    assert not marker.exists()


def test_repository_config_cannot_run_an_fsmonitor_program(tool, repo):
    script, marker = _plant_program(repo, "fsmonitor")
    git(repo, "config", "core.fsmonitor", str(script))
    subprocess.run(["git", "-C", str(repo), "status"], capture_output=True)
    if not marker.exists():
        pytest.skip("this git version doesn't run core.fsmonitor here")
    marker.unlink()
    run(tool, command="status")
    run(tool, command="diff")
    assert not marker.exists()


def test_is_tracked(repo, tmp_path_factory):
    ws = Workspace(repo)
    assert is_tracked(ws, repo / "app.py") is True
    assert is_tracked(ws, repo / "x_client.py") is False
    plain = tmp_path_factory.mktemp("plain")
    (plain / "f.txt").write_text("x")
    assert is_tracked(Workspace(plain), plain / "f.txt") is None


# ----- git commands typed into the `command` field ---------------------------------

def test_a_typed_diff_with_a_ref_is_understood(repo, tool):
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "Second")
    out = run(tool, command="diff HEAD~1")
    assert "x_client.py" in out.output and "a + b + 0" in out.output


def test_a_leading_git_and_flags_are_accepted(repo, tool):
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "Second")
    out = run(tool, command="git log --oneline -1")
    assert "Second" in out.output and "Add the add function" not in out.output


def test_typed_log_limit_forms(repo, tool):
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "Second")
    for form in ("log -1", "log -n1", "log -n 1"):
        assert out_commits(run(tool, command=form)) == 1, form
    assert out_commits(run(tool, command="log -2")) == 2


def out_commits(result):
    return int(result.summary.split()[0])


def test_a_typed_range_and_a_typed_path(repo, tool):
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "Second")
    out = run(tool, command="diff HEAD~1 -- app.py")
    assert "app.py" in out.output and "x_client.py" not in out.output
    assert "a + b + 0" in run(tool, command="show HEAD app.py").output


def test_two_typed_refs_for_diff_mean_a_range(repo, tool):
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "Second")
    out = run(tool, command="diff HEAD~1 HEAD")
    assert "x_client.py" in out.output


def test_a_typed_ref_beside_a_path_argument(repo, tool):
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "Second")
    out = run(tool, command="diff HEAD~1", path="app.py")
    assert "a + b + 0" in out.output and "x_client.py" not in out.output


@pytest.mark.parametrize("typed", [
    "diff --output=/tmp/pwned", "log --exec=touch", "diff -c core.pager=sh", "status; touch /tmp/x",
    "show HEAD && rm -rf /", "checkout main", "git commit -m x", "push origin main", "diff $(id)", "log | cat",
])
def test_typed_commands_cannot_do_more_than_the_tool_does(repo, tool, typed):
    with pytest.raises(ToolError):
        run(tool, command=typed)
    assert not os.path.exists("/tmp/pwned")


def test_an_unknown_typed_subcommand_names_the_allowed_ones(tool):
    with pytest.raises(ToolError, match="status, diff, log or show"):
        run(tool, command="checkout main")


@pytest.mark.parametrize('typed', ['commit', 'add', 'checkout main', 'git commit -m "merge"', 'git add pricing.py'])
def test_write_commands_explain_read_only_and_shell(tool, repo, typed):
    before = git(repo, 'status', '--porcelain').stdout
    with pytest.raises(ToolError) as exc:
        run(tool, command=typed)
    msg = str(exc.value)
    assert 'read-only' in msg
    assert 'run_shell' in msg and 'git add' in msg
    assert git(repo, 'status', '--porcelain').stdout == before
