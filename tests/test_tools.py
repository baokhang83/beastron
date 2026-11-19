import os

import pytest

from beastron.tools import ToolError, Workspace, make_tools


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


def test_edit_not_found_shows_closest_text(tools, ws):
    (ws.root / "ui.py").write_text(
        "class UI:\n"
        "    def header(self):\n"
        "        line = Text()\n"
        "        line.append(' beastron ', style='bold')\n"
        "        return line\n"
    )
    # wrong indentation and a slightly different string, as small models produce
    wrong = "line = Text()\nline.append(' beastron ', style='bold cyan')"
    with pytest.raises(ToolError) as err:
        tools["edit_file"].preview({"path": "ui.py", "old_text": wrong, "new_text": "x"})
    message = str(err.value)
    assert "closest text in the file is lines 3-4" in message
    assert "        line.append(' beastron ', style='bold')" in message  # real indentation shown


def test_edit_not_found_without_a_close_match(tools):
    with pytest.raises(ToolError) as err:
        tools["edit_file"].preview({"path": "src/app.py", "old_text": "completely unrelated text here", "new_text": "x"})
    assert "closest" not in str(err.value)
    assert "Read the file again" in str(err.value)


def test_delete_file(tools, ws):
    delete = tools["delete_file"]
    preview = delete.preview({"path": "src/app.py"})
    assert "not in a git repository: cannot be restored" in preview.title
    assert "-    return a - b" in preview.body  # the whole file shown as removed
    result = delete.run({"path": "src/app.py"})
    assert not (ws.root / "src/app.py").exists()
    assert result.change.kind == "deleted" and result.change.removed == 4


def test_delete_file_refuses_dirs_missing_files_and_git(tools, ws):
    delete = tools["delete_file"]
    with pytest.raises(ToolError, match="directory"):
        delete.preview({"path": "src"})
    with pytest.raises(ToolError, match="not found"):
        delete.preview({"path": "nope.py"})
    with pytest.raises(ToolError, match=".git"):
        delete.preview({"path": ".git/config"})
    with pytest.raises(ToolError, match="outside the project"):
        delete.preview({"path": "../elsewhere.py"})


def test_file_tools_report_their_changes(tools):
    created = tools["write_file"].run({"path": "new.py", "content": "a\nb\n"})
    assert (created.change.kind, created.change.added, created.change.removed) == ("created", 2, 0)
    edited = tools["edit_file"].run({"path": "new.py", "old_text": "b", "new_text": "c"})
    assert (edited.change.kind, edited.change.added, edited.change.removed) == ("modified", 1, 1)
    assert tools["read_file"].run({"path": "new.py"}).change is None


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
