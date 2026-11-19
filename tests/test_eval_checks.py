import pytest

from firox.evals.checks import CheckError, Context, changed_paths, evaluate, parse_checks, snapshot


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "notes.md").write_text("keep me\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nx-handles = "app:main"\n')
    return tmp_path


def run(ws, *checks, answer="", before=None):
    ctx = Context(ws, answer, snapshot(ws) if before is None else before)
    return evaluate(parse_checks(list(checks)), ctx)


def one(ws, check, **kw):
    [result] = run(ws, check, **kw)
    return result


# ----- each type passes and fails, and failure reasons name the file or fact --

def test_command(ws):
    assert one(ws, {"type": "command", "run": "test -f src/app.py"}).passed
    failed = one(ws, {"type": "command", "run": "echo 'AssertionError: 1 != 2' >&2; exit 1"})
    assert not failed.passed
    assert "exited with 1, expected 0" in failed.reason and "AssertionError: 1 != 2" in failed.reason
    assert one(ws, {"type": "command", "run": "exit 3", "exit_code": 3}).passed


def test_command_timeout(ws):
    result = one(ws, {"type": "command", "run": "sleep 5", "timeout": 1})
    assert not result.passed and "timed out after 1s" in result.reason


def test_file_exists_and_absent(ws):
    assert one(ws, {"type": "file_exists", "path": "src/app.py"}).passed
    missing = one(ws, {"type": "file_exists", "path": "src/x_client.py"})
    assert not missing.passed and "src/x_client.py does not exist" in missing.reason
    assert one(ws, {"type": "file_absent", "path": "src/x_client.py"}).passed
    present = one(ws, {"type": "file_absent", "path": "notes.md"})
    assert not present.passed and "notes.md still exists" in present.reason


def test_contains_and_not_contains(ws):
    assert one(ws, {"type": "contains", "path": "src/app.py", "text": "return a + b"}).passed
    assert one(ws, {"type": "contains", "path": "src/app.py", "text": r"return\s+a \+ b", "regex": True}).passed
    no = one(ws, {"type": "contains", "path": "src/app.py", "text": "return a - b"})
    assert not no.passed and "src/app.py does not contain 'return a - b'" in no.reason
    assert one(ws, {"type": "not_contains", "path": "pyproject.toml", "text": "y-handles"}).passed
    still = one(ws, {"type": "not_contains", "path": "pyproject.toml", "text": "x-handles"})
    assert not still.passed and "pyproject.toml still contains 'x-handles'" in still.reason
    gone = one(ws, {"type": "contains", "path": "gone.py", "text": "x"})
    assert not gone.passed and "gone.py does not exist" in gone.reason


def test_unchanged_compares_with_the_snapshot_taken_before_the_run(ws):
    before = snapshot(ws)
    (ws / "src" / "app.py").write_text("changed\n")  # what the agent did during the run
    assert one(ws, {"type": "unchanged", "paths": ["notes.md"]}, before=before).passed
    result = one(ws, {"type": "unchanged", "paths": ["src"]}, before=before)  # a folder covers its files
    assert not result.passed and "src/app.py" in result.reason


def test_no_changes_for_questions_ignores_caches(ws):
    before = snapshot(ws)
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\0")  # running the tests creates these
    (ws / ".pytest_cache").mkdir()
    (ws / ".pytest_cache" / "v").write_text("x")
    assert one(ws, {"type": "no_changes"}, before=before).passed
    (ws / "src" / "new.py").write_text("x = 1\n")
    result = one(ws, {"type": "no_changes"}, before=before)
    assert not result.passed and "src/new.py" in result.reason
    assert one(ws, {"type": "no_changes", "ignore": ["src/*.py"]}, before=before).passed


def test_only_changed(ws):
    before = snapshot(ws)
    (ws / "src" / "app.py").write_text("changed\n")
    (ws / "notes.md").unlink()  # deleting counts as a change
    assert one(ws, {"type": "only_changed", "paths": ["src", "notes.md"]}, before=before).passed
    result = one(ws, {"type": "only_changed", "paths": ["src/*.py"]}, before=before)
    assert not result.passed and "notes.md" in result.reason


def test_answer_mentions(ws):
    answer = "FluencyLoop captures the decisions behind each feature while your agent builds it."
    assert one(ws, {"type": "answer_mentions", "patterns": ["decisions", r"agent\s+builds"]}, answer=answer).passed
    result = one(ws, {"type": "answer_mentions", "patterns": ["decisions", "diagram"]}, answer=answer)
    assert not result.passed and "/diagram/" in result.reason and "decisions" not in result.reason.split(":")[1]


def test_all_checks_are_evaluated_and_reported(ws):
    results = run(ws, {"type": "file_exists", "path": "notes.md"}, {"type": "file_exists", "path": "nope"})
    assert [r.passed for r in results] == [True, False]


# ----- snapshots ------------------------------------------------------------------

def test_snapshot_hashes_files_and_skips_git_and_caches(ws):
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.js").write_text("x")
    snap = snapshot(ws)
    assert set(snap) == {"notes.md", "pyproject.toml", "src/app.py"}
    assert len(snap["notes.md"]) == 64
    (ws / "notes.md").write_text("edited\n")
    assert changed_paths(snap, snapshot(ws)) == ["notes.md"]


# ----- authoring errors ------------------------------------------------------------

@pytest.mark.parametrize("check, message", [
    ({"type": "magic"}, "type 'magic' is not one of"),
    ({"type": "file_exists"}, "missing ['path']"),
    ({"type": "file_exists", "path": "a", "colour": "blue"}, "unknown fields ['colour']"),
    ({"type": "file_exists", "path": "../outside"}, "must stay inside the workspace"),
    ({"type": "file_exists", "path": "/etc/passwd"}, "must stay inside the workspace"),
    ({"type": "contains", "path": "a", "text": "(", "regex": True}, "invalid regex"),
    ({"type": "answer_mentions", "patterns": []}, "'patterns' can't be empty"),
    ({"type": "command", "run": "true", "timeout": "soon"}, "'timeout' must be a whole number"),
])
def test_badly_written_checks_are_rejected(check, message):
    with pytest.raises(CheckError, match=message.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(")):
        parse_checks([check])
