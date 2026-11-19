import hashlib
import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from firox.agent import Agent
from firox.cli import app
from firox.client import ModelReply
from firox.quirks import ToolCall
from firox.tools import Workspace, make_tools
from firox.trace import SessionTrace, digest_from_tags, fan_out, model_digest, repo_state
from test_agent import PROFILE, FakeClient

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    path = tmp_path / "state"
    monkeypatch.setenv("FIROX_STATE_DIR", str(path))
    return path


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def run_turn(project, replies):
    ws = Workspace(project)
    trace = SessionTrace.start(ws, PROFILE)
    agent = Agent(FakeClient(replies), ws, make_tools(ws), on_event=fan_out(None, trace), approve=lambda r: True)
    agent.run("create notes.txt")
    trace.close()
    return records(trace.path)


def test_a_session_file_with_header_events_and_turn_end(tmp_path, state_dir):
    project = tmp_path / "project"
    project.mkdir()
    lines = run_turn(project, [
        ModelReply(tool_calls=[ToolCall("c1", "write_file", '{"path": "notes.txt", "content": "hi\\n"}')]),
        ModelReply(content="Created notes.txt."),
    ])
    files = list((state_dir / "sessions").glob("*.jsonl"))
    assert len(files) == 1 and "_project_" in files[0].name

    header = lines[0]
    assert header["type"] == "session" and header["cwd"] == str(project.resolve())
    assert header["model"]["profile"] == "fake" and "digest" in header["model"]
    assert "version" in header["firox"] and "commit" in header["firox"]

    types = [r["type"] for r in lines]
    assert types[1] == "turn_start" and lines[1]["task"] == "create notes.txt"
    assert types[-1] == "session_end"
    # streamed text is combined into one record per model step
    outputs = [r for r in lines if r["type"] == "model_output"]
    assert [o["text"] for o in outputs] == ["", "Created notes.txt."]
    assert "text_delta" not in types and "reasoning_delta" not in types
    # tool calls with their full output, and the change record at the end of the turn
    tool_end = next(r for r in lines if r["type"] == "tool_end")
    assert tool_end["name"] == "write_file" and tool_end["output"].startswith("Created notes.txt")
    turn_end = next(r for r in lines if r["type"] == "turn_end")
    assert turn_end["reason"] == "done"
    assert turn_end["changes"] == [{"path": "notes.txt", "kind": "created", "added": 1, "removed": 0}]
    assert all("t" in r for r in lines[1:])


def test_repo_state_for_git_projects(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(project), *a], check=True, capture_output=True, env=GIT_ENV)
    git("init", "-q", "-b", "main")
    (project / "app.py").write_text("x = 1\n")
    git("add", "app.py")
    git("commit", "-q", "-m", "init")
    (project / "app.py").write_text("x = 2\n")
    (project / "new.py").write_text("print('new')\n")

    state = repo_state(Workspace(project))
    assert len(state["head"]) == 40 and state["branch"] == "main"
    assert "-x = 1" in state["diff"] and "+x = 2" in state["diff"]
    expected_sha = hashlib.sha256(b"print('new')\n").hexdigest()
    assert state["untracked"] == [{"path": "new.py", "size": 13, "sha256": expected_sha}]


def test_repo_state_is_skipped_outside_git(tmp_path):
    assert repo_state(Workspace(tmp_path)) is None


def test_digest_from_ollama_tags():
    tags = {"models": [
        {"name": "qwen3:8b", "model": "qwen3:8b", "digest": "500a1f067a9f"},
        {"name": "gpt-oss:latest", "model": "gpt-oss:latest", "digest": "abc"},
    ]}
    assert digest_from_tags(tags, "qwen3:8b") == "500a1f067a9f"
    assert digest_from_tags(tags, "gpt-oss") == "abc"  # no tag means :latest, as in Ollama
    assert digest_from_tags(tags, "qwen3:14b") is None


def test_digest_is_none_when_the_server_is_unreachable_or_not_ollama():
    from dataclasses import replace
    assert model_digest(replace(PROFILE, base_url="http://127.0.0.1:9/v1")) is None
    assert model_digest(replace(PROFILE, base_url="https://example.com/api")) is None  # not an Ollama-style URL


def _cli(tmp_path, monkeypatch, *flags):
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "models.toml"
    # a server that refuses connections: the turn fails fast, offline
    config.write_text('[models.local]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\n')
    monkeypatch.chdir(project)
    return CliRunner().invoke(app, ["--config", str(config), *flags, "say hi"])


def test_cli_writes_a_trace(tmp_path, monkeypatch, state_dir):
    result = _cli(tmp_path, monkeypatch)
    assert result.exit_code == 1  # the model server is unreachable
    [path] = (state_dir / "sessions").glob("*.jsonl")
    types = [r["type"] for r in records(path)]
    assert types[0] == "session" and "turn_start" in types and "error_event" in types
    assert types[-1] == "session_end"
    assert "session trace:" in result.output


def test_cli_no_trace(tmp_path, monkeypatch, state_dir):
    result = _cli(tmp_path, monkeypatch, "--no-trace")
    assert result.exit_code == 1
    assert not (state_dir / "sessions").exists()
    assert "session trace:" not in result.output
