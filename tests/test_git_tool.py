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
