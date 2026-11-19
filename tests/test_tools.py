import os

import pytest

from firox.tools import ToolError, Workspace, make_tools


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n\nprint(add(1, 2))\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("x = 1\n")
    return Workspace(tmp_path)


@pytest.fixture
def tools(ws):
    return {t.name: t for t in make_tools(ws)}


def test_paths_outside_workspace_are_blocked(ws, tmp_path):
    with pytest.raises(ToolError, match="outside the project"):
        ws.resolve("../secret.txt")
    with pytest.raises(ToolError, match="outside the project"):
        ws.resolve("/etc/passwd")


def test_symlink_escape_is_blocked(ws, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    os.symlink(outside, tmp_path / "link")
    with pytest.raises(ToolError, match="outside the project"):
        ws.resolve("link/file.txt")


def test_writes_to_git_are_blocked(ws):
    ws.resolve(".git/config")  # reading is allowed
    with pytest.raises(ToolError, match=".git"):
        ws.resolve(".git/config", for_write=True)


def test_list_files_skips_ignored_dirs(tools):
    result = tools["list_files"].run({})
    assert "src/app.py" in result.output
    assert ".venv" not in result.output


def test_search_code(tools):
    result = tools["search_code"].run({"pattern": r"return a [-+] b"})
    assert result.output == "src/app.py:2: return a - b"
    # invalid regex falls back to a plain-text search
    assert "No matches" in tools["search_code"].run({"pattern": "add(("}).output


def test_read_file_with_offset(tools):
    full = tools["read_file"].run({"path": "src/app.py"})
    assert full.summary == "4 lines"
    part = tools["read_file"].run({"path": "src/app.py", "offset": 2, "limit": 1})
    assert part.output.startswith("    return a - b")
    assert "showing lines 2-2 of 4" in part.output
    assert "offset=3" in part.output


def test_edit_file_exact_match(tools, ws):
    edit = tools["edit_file"]
    args = {"path": "src/app.py", "old_text": "return a - b", "new_text": "return a + b"}
    preview = edit.preview(args)
    assert "-    return a - b" in preview.body and "+    return a + b" in preview.body
    result = edit.run(args)
    assert result.summary == "+1 -1"
    assert "return a + b" in (ws.root / "src/app.py").read_text()


def test_edit_file_rejects_missing_and_ambiguous_text(tools, ws):
    edit = tools["edit_file"]
    with pytest.raises(ToolError, match="not found"):
        edit.preview({"path": "src/app.py", "old_text": "nope", "new_text": "x"})
    (ws.root / "dup.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="appears 2 times"):
        edit.preview({"path": "dup.py", "old_text": "x = 1", "new_text": "x = 2"})


def test_write_file_creates_parent_dirs(tools, ws):
    tools["write_file"].run({"path": "new/dir/hello.py", "content": "print('hi')\n"})
    assert (ws.root / "new/dir/hello.py").read_text() == "print('hi')\n"


def test_run_shell_reports_exit_code(tools):
    ok = tools["run_shell"].run({"command": "echo hello"})
    assert ok.ok and "hello" in ok.output and ok.summary.startswith("exit 0")
    bad = tools["run_shell"].run({"command": "echo oops >&2; exit 3"})
    assert not bad.ok and "exit code: 3" in bad.output and "oops" in bad.output


def test_run_shell_timeout(tools):
    result = tools["run_shell"].run({"command": "sleep 5", "timeout": 1})
    assert not result.ok and "Timed out" in result.output


def test_check_args_coerces_and_reports(tools):
    read = tools["read_file"]
    assert read.check_args({"path": "a", "offset": "5"})["offset"] == 5
    with pytest.raises(ToolError, match="Missing required argument"):
        read.check_args({})
