"""Reading Codex session logs, with small synthetic logs."""

import json

from typer.testing import CliRunner

import firox.usecases.codex_logs as codex_logs
from firox.cli import app
from firox.usecases.codex_logs import read_all, read_session
from firox.usecases.requests import load


def rec(kind, payload, ts="2026-11-10T05:48:27Z"):
    return {"timestamp": ts, "type": kind, "payload": payload}


def meta(cwd="/work/shop", id="0199-abc"):
    return rec("session_meta", {"id": id, "cwd": cwd, "source": "cli"})


def typed(text):
    return rec("event_msg", {"type": "user_message", "message": text})


def exec_script(*commands, extra=""):
    calls = ",\n".join(f'tools.exec_command({{cmd:{json.dumps(c)}, workdir:"/work/shop"}})' for c in commands)
    return rec("response_item", {"type": "custom_tool_call", "name": "exec", "input": f"const r = await Promise.all([\n{calls}\n]);{extra}"})


def write(root, name, records):
    path = root / "2026" / "11" / "10" / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + "{broken\n")
    return path


SESSION = [
    meta(),
    rec("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>…"}]}),
    typed("Fix the failing Maven build in the cart module"),
    exec_script("cd shop && mvn -q -o test | tail -30", 'rg -n "total" src', extra=" await tools.wait({ms: 100});"),
    rec("response_item", {"type": "custom_tool_call", "name": "apply_patch", "input": "*** Begin Patch"}),
    rec("response_item", {"type": "function_call", "name": "exec_command",
                          "arguments": json.dumps({"cmd": "git status && gh pr view 7", "workdir": "/work/shop"})}),
    rec("turn_context", {"cwd": "/work/shop/cart"}),
    typed("revert that, it's wrong"),
    typed("/review"),
]


def test_requests_and_their_tools(tmp_path):
    first, revert, review = read_session(write(tmp_path, "a", SESSION))
    assert first.text == "Fix the failing Maven build in the cart module"
    assert (first.source, first.session, first.project, first.position) == ("codex", "0199-abc", "/work/shop", 0)
    assert first.tools == {"exec_command": 3, "wait": 1, "apply_patch": 1}
    assert first.shell == {"cd": 1, "mvn": 1, "tail": 1, "rg": 1, "git": 1, "gh": 1}
    assert first.corrected  # the next request was "revert that"
    assert revert.project == "/work/shop/cart"  # turn_context moved the session
    assert (review.command, review.position) == ("/review", 2)


def test_injected_context_messages_are_not_requests(tmp_path):
    texts = [r.text for r in read_session(write(tmp_path, "b", SESSION))]
    assert not any("environment_context" in t for t in texts)


def test_an_excluded_session_is_closed_after_its_first_line(tmp_path, monkeypatch):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text("/work/secret\n")
    monkeypatch.setenv("FIROX_EXCLUDE", str(exclude))
    parsed = []
    original = codex_logs._parse
    monkeypatch.setattr(codex_logs, "_parse", lambda line: parsed.append(line) or original(line))
    assert read_session(write(tmp_path, "c", [meta(cwd="/work/secret/app"), typed("private")])) == []
    assert len(parsed) == 1  # only the session_meta line was read


def test_a_session_moving_into_an_excluded_folder_is_dropped(tmp_path, monkeypatch):
    exclude = tmp_path / "exclude.txt"
    exclude.write_text("/work/secret\n")
    monkeypatch.setenv("FIROX_EXCLUDE", str(exclude))
    records = [meta(), typed("hello"), rec("turn_context", {"cwd": "/work/secret"}), typed("private")]
    assert read_session(write(tmp_path, "d", records)) == []


def test_read_all_and_the_scan_command(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    write(root, "a", SESSION)
    write(root, "b", [meta(id="other"), typed("what does this repo do?")])
    assert len(read_all(root)) == 4
    monkeypatch.setenv("FIROX_CODEX_DIR", str(root))
    monkeypatch.setenv("FIROX_CLAUDE_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("FIROX_STATE_DIR", str(tmp_path / "state"))
    result = CliRunner().invoke(app, ["usecases", "scan"])
    assert result.exit_code == 0, result.output
    assert "Codex: 4 requests in 2 sessions" in result.output and "Claude Code: 0 requests" in result.output
    assert "Maven" not in result.output  # counts only
    assert len(load(tmp_path / "state" / "usecases" / "codex.jsonl")) == 4
