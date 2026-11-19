"""The Claude Code reference runner, driven by a fake `claude` (no account, network or sandbox needed)."""

import json
import os
import stat
import sys
import textwrap
from dataclasses import asdict

import pytest
from typer.testing import CliRunner

import beastron.cli as cli
from beastron.evals import reference
from beastron.evals.report import compare, summarize
from test_eval_runner import lines, make_task, run_dir  # noqa: F401  (run_dir is a fixture)

FAKE = textwrap.dedent('''\
    #!{python}
    """A stand-in for Claude Code. FAKE_CLAUDE_MODE picks what it does."""
    import json, os, sys, time
    from pathlib import Path

    if sys.argv[1:] == ["--version"]:
        print(os.environ.get("FAKE_CLAUDE_VERSION", "9.9.9") + " (Claude Code)")
        sys.exit(0)
    Path(os.environ["FAKE_CLAUDE_ARGS"]).write_text(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(),
        "nested": "CLAUDECODE" in os.environ}}))
    mode = os.environ.get("FAKE_CLAUDE_MODE", "pass")
    model = sys.argv[sys.argv.index("--model") + 1]

    def emit(event):
        print(json.dumps(event), flush=True)

    emit({{"type": "system", "subtype": "init", "model": model, "claude_code_version": "9.9.9"}})
    if mode == "crash":
        print("boom: not logged in", file=sys.stderr)
        sys.exit(1)
    if mode == "hang":
        time.sleep(60)
    if mode == "pass":
        emit({{"type": "assistant", "message": {{"content": [
            {{"type": "tool_use", "name": "Write", "input": {{}}}}, {{"type": "tool_use", "name": "Bash", "input": {{}}}}]}}}})
        Path("notes.txt").write_text("hello\\n")
        emit({{"type": "user", "message": {{"content": [
            {{"type": "tool_result", "is_error": False}}, {{"type": "tool_result", "is_error": True}}]}}}})
    print("not json", flush=True)
    emit({{"type": "result", "subtype": "error_max_turns" if mode == "max_turns" else "success",
           "is_error": mode == "max_turns", "num_turns": 3, "duration_api_ms": 2500,
           "result": "Created notes.txt for the demo README.", "total_cost_usd": 0.03,
           "usage": {{"input_tokens": 4, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 900,
                      "output_tokens": 50}},
           "modelUsage": {{model: {{}}, "claude-haiku-5-5": {{}}}}}})
''')


@pytest.fixture
def claude(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    args = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_CLAUDE_ARGS", str(args))
    monkeypatch.setenv("CLAUDECODE", "1")  # as when beastron itself runs inside a Claude Code session
    return path, args


def run(task, run_dir, claude, mode="pass", monkeypatch=None, **kwargs):
    if monkeypatch:
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    return reference.run_reference_task(task, claude=str(claude[0]), run_dir=run_dir, use_sandbox=False,
                                        version="9.9.9", **kwargs)


def test_a_passing_run_is_recorded_like_a_beastron_run(tmp_path, run_dir, claude, monkeypatch):
    task = make_task(tmp_path)
    result = run(task, run_dir, claude, "pass", monkeypatch)
    assert result.passed and result.reason == "done" and result.error is None
    assert result.profile == "claude-code-sonnet-5-5" and result.model == "claude-sonnet-5-5"
    assert result.claude_code_version == "9.9.9"
    assert result.models_used == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert (result.steps, result.prompt_tokens, result.completion_tokens) == (3, 1004, 50)
    assert (result.tool_calls, result.tool_errors, result.shell_commands) == (2, 1, 1)
    assert result.model_seconds == 2.5 and result.cost_usd == 0.03
    [line] = lines(run_dir)
    assert line["task"] == "make-notes" and line["passed"] is True
    assert line["trace"].startswith(str(run_dir / "traces"))


def test_claude_code_is_called_headless_in_the_workspace_without_the_owners_setup(tmp_path, run_dir, claude, monkeypatch):
    run(make_task(tmp_path), run_dir, claude, "pass", monkeypatch)
    seen = json.loads(claude[1].read_text())
    argv = seen["argv"]
    assert argv[:2] == ["-p", "Create notes.txt"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    for flag in ("--safe-mode", "--no-session-persistence", "--dangerously-skip-permissions", "--verbose"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert seen["cwd"].endswith("project") and "beastron-eval-make-notes-" in seen["cwd"]
    assert seen["nested"] is False  # the parent session's variables are removed


def test_the_final_message_is_the_answer(tmp_path, run_dir, claude, monkeypatch):
    checks = '[[checks]]\ntype = "answer_mentions"\npatterns = ["demo", "readme"]\n'
    result = run(make_task(tmp_path, checks=checks), run_dir, claude, "answer", monkeypatch)
    assert result.passed, result.checks


def test_a_failing_check_fails_the_run(tmp_path, run_dir, claude, monkeypatch):
    result = run(make_task(tmp_path), run_dir, claude, "answer", monkeypatch)
    assert not result.passed and result.reason == "done"
    assert result.checks[0]["reason"] == "notes.txt does not exist"


def test_a_crash_is_an_error_with_its_message(tmp_path, run_dir, claude, monkeypatch):
    result = run(make_task(tmp_path), run_dir, claude, "crash", monkeypatch)
    assert not result.passed and result.reason == "error"
    assert "no result from Claude Code (exit code 1)" in result.error and "not logged in" in result.error
    assert len(lines(run_dir)) == 1


def test_running_out_of_turns_is_max_steps(tmp_path, run_dir, claude, monkeypatch):
    result = run(make_task(tmp_path), run_dir, claude, "max_turns", monkeypatch)
    assert result.reason == "max_steps" and not result.passed


def test_the_time_limit_kills_the_session(tmp_path, run_dir, claude, monkeypatch):
    task = make_task(tmp_path, toml_extra="timeout = 1\n")
    result = run(task, run_dir, claude, "hang", monkeypatch)
    assert result.reason == "timeout" and not result.passed
    assert result.seconds < 30


def test_the_suite_records_the_version_once_and_every_run(tmp_path, claude, monkeypatch):
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "1.2.3")
    run_dir, results = reference.run_reference_suite([make_task(tmp_path)], claude=str(claude[0]), repeats=2,
                                                     use_sandbox=False)
    assert [r.repeat for r in results] == [1, 2]
    info = json.loads((run_dir / "run.json").read_text())
    assert info["reference"]["model"] == "claude-sonnet-5-5"
    assert info["reference"]["claude_code_version"] == "1.2.3"
    # the session's own report wins over `claude --version` when both exist
    assert {r.claude_code_version for r in results} == {"9.9.9"}


def test_a_changed_claude_code_version_is_flagged(tmp_path, run_dir, claude, monkeypatch):
    first = asdict(run(make_task(tmp_path), run_dir, claude, "pass", monkeypatch))
    second = dict(first, claude_code_version="10.0.0")
    [base] = summarize([first])
    [now] = summarize([second])
    assert base["claude_code_versions"] == ["9.9.9"]
    cmp = compare(now, base)
    assert cmp.claude_code_changed and not cmp.has_regressions
    assert any("Claude Code changed since the baseline: 9.9.9 → 10.0.0" in n for n in cmp.notes)
    assert not compare(base, base).claude_code_changed


def test_beastron_summaries_have_no_claude_code_version(tmp_path, run_dir, claude, monkeypatch):
    plain = dict(asdict(run(make_task(tmp_path), run_dir, claude, "pass", monkeypatch)), claude_code_version=None)
    [s] = summarize([plain])
    assert s["claude_code_versions"] == [] and not compare(s, s).claude_code_changed


def test_the_command_runs_and_saves_a_summary(tmp_path, claude, monkeypatch):
    task = make_task(tmp_path)
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "pass")
    monkeypatch.chdir(tmp_path)
    results = tmp_path / "results"
    out = CliRunner().invoke(cli.app, ["eval", "reference", "--tasks-dir", str(task.dir.parent), "-n", "1",
                                       "--claude", str(claude[0]), "--unsafe-no-sandbox", "--results-dir", str(results),
                                       "--baselines-dir", str(tmp_path / "baselines")])
    assert out.exit_code == 0, out.output
    assert "1 Claude Code sessions" in out.output and "Claude Code 9.9.9" in out.output
    [summary] = list(results.glob("*_claude-code-sonnet-5-5_*.json"))
    assert json.loads(summary.read_text())["claude_code_versions"] == ["9.9.9"]


def test_the_command_needs_claude(tmp_path, monkeypatch):
    task = make_task(tmp_path)
    out = CliRunner().invoke(cli.app, ["eval", "reference", "--tasks-dir", str(task.dir.parent),
                                       "--claude", str(tmp_path / "missing")])
    assert out.exit_code == 1 and "not found" in out.output


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec exists only on macOS")
def test_the_sandbox_lets_claude_code_write_its_state_but_not_elsewhere(tmp_path):
    import subprocess

    prefix = reference.sandbox_prefix(tmp_path)
    home = os.path.expanduser("~")
    probe = f"{home}/.claude.json.beastron-sandbox-test"
    try:
        ok = subprocess.run(prefix + ["/bin/sh", "-c", f"echo x > '{probe}'"], capture_output=True)
        assert ok.returncode == 0, ok.stderr
    finally:
        if os.path.exists(probe):
            os.remove(probe)
    denied = subprocess.run(prefix + ["/bin/sh", "-c", f"echo x > '{home}/beastron-sandbox-test'"], capture_output=True)
    assert denied.returncode != 0
    assert not os.path.exists(f"{home}/beastron-sandbox-test")
