"""Reading Claude Code session logs, with small synthetic logs."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firox.cli import app
from firox.usecases.claude_logs import encode_folder, read_all, read_session
from firox.usecases.requests import _shell_words, load


def user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, "cwd": "/work/shop",
            "timestamp": "2026-11-01T10:00:00Z", **extra}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def assistant(*tools, text=None):
    blocks = [{"type": "text", "text": text}] if text else []
    for name, tool_input in tools:
        blocks.append({"type": "tool_use", "name": name, "input": tool_input})
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def write_session(root, folder, name, records):
    path = root / folder / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + "not json\n")
    return path


SESSION = [
    user("The checkout test fails, can you fix it?"),
    assistant(("Bash", {"command": "cd shop && mvn -q test | tail -20"}), ("Read", {"file_path": "Cart.java"})),
    tool_result(),
    assistant(("Edit", {"file_path": "Cart.java"}), ("Bash", {"command": "FOO=1 git status && gh pr list"})),
    tool_result(),
    user("<task-notification>a background command finished</task-notification>"),  # harness, not a request
    user("hidden", isMeta=True),
    user("sub-agent prompt", isSidechain=True),
    user("This session is being continued from a previous conversation that ran out of context."),
    user("<command-message>loop</command-message>\n<command-name>/loop</command-name>\n<command-args>check CI</command-args>"),
    user([{"type": "text", "text": "what does Cart.total do?"}]),
    assistant(text="It adds the line totals."),
    user("no, that's wrong"),
]


def test_requests_and_their_tools(tmp_path):
    [first, loop, question, correction] = read_session(write_session(tmp_path, "-work-shop", "s1", SESSION))
    assert first.text == "The checkout test fails, can you fix it?" and first.position == 0
    assert first.tools == {"Bash": 2, "Read": 1, "Edit": 1}
    assert first.shell == {"cd": 1, "mvn": 1, "tail": 1, "git": 1, "gh": 1}
    assert first.project == "/work/shop" and first.source == "claude" and first.session == "s1"
    assert (loop.command, loop.text) == ("/loop", "check CI")
    assert question.text == "what does Cart.total do?" and question.tools == {}
    assert question.corrected and not first.corrected  # the next request was "no, that's wrong"
    assert correction.text == "no, that's wrong"


def test_pasted_text_is_kept_without_its_tags():
    from firox.usecases.claude_logs import _request_text
    text, _ = _request_text('<pasted_content id="x">\nTraceback ...\n</pasted_content id="x">\nwhat is this?')
    assert text == "Traceback ...\n\nwhat is this?"


def test_shell_words_ignore_heredoc_bodies_and_keywords():
    assert _shell_words("cat > f.py <<'EOF'\nimport os\nreturn 1\n}\nEOF") == ["cat"]
    assert _shell_words("if [ -f x ]; then make; fi") == ["make"]  # keywords and test brackets aren't commands
    assert _shell_words("/usr/bin/git log | head -3") == ["git", "head"]


def test_excluded_and_observer_sessions_are_never_opened(tmp_path, monkeypatch):
    excluded_project = tmp_path / "Documents" / "secretproject"
    exclude = tmp_path / "exclude.txt"
    exclude.write_text(f"{excluded_project}\n")
    monkeypatch.setenv("FIROX_EXCLUDE", str(exclude))
    root = tmp_path / "projects"
    write_session(root, "-work-shop", "kept", SESSION[:2])
    for folder in (encode_folder(excluded_project.resolve()), "-Users-x--claude-mem-observer-sessions"):
        path = write_session(root, folder, "secret", [user("private")])
        path.chmod(0)  # opening it would fail the test
    try:
        requests = read_all(root)
    finally:
        for path in root.rglob("secret.jsonl"):
            path.chmod(0o600)
    assert [r.session for r in requests] == ["kept"]


def test_a_session_that_ran_in_an_excluded_folder_is_dropped(tmp_path, monkeypatch):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text("/work/secret\n")
    monkeypatch.setenv("FIROX_EXCLUDE", str(exclude))
    session = [user("hi", cwd="/work/secret/sub")]
    assert read_session(write_session(tmp_path, "-elsewhere", "s", session)) == []


def test_scan_writes_only_to_the_local_state_folder(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    write_session(root, "-work-shop", "s1", SESSION)
    state = tmp_path / "state"
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("FIROX_CLAUDE_DIR", str(root))
    monkeypatch.setenv("FIROX_STATE_DIR", str(state))
    result = CliRunner().invoke(app, ["usecases", "scan"])
    assert result.exit_code == 0, result.output
    assert "4 requests in 1 sessions" in result.output
    assert "fix it" not in result.output  # counts only, never prompt text
    saved = state / "usecases" / "claude.jsonl"
    assert len(load(saved)) == 4
    assert oct(saved.stat().st_mode & 0o777) == "0o600"
    assert list(project.iterdir()) == []  # nothing written in the current project
