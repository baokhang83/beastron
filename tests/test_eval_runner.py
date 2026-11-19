"""The headless eval runner, driven by a scripted model (no server needed)."""

import json
import time
import uuid
from pathlib import Path

import pytest

from beastron.client import ModelError, ModelReply
from beastron.evals import sandbox
from beastron.evals.runner import run_suite, run_task
from beastron.evals.task import load_task
from beastron.quirks import ToolCall
from test_agent import PROFILE, FakeClient


def make_task(root, toml_extra="", checks=None):
    task_dir = root / "tasks" / "make-notes"
    (task_dir / "repo").mkdir(parents=True)
    (task_dir / "repo" / "README.md").write_text("# Demo\n")
    checks = checks or '[[checks]]\ntype = "file_exists"\npath = "notes.txt"\n'
    (task_dir / "task.toml").write_text(
        'prompt = "Create notes.txt"\ncategory = "feature"\nlanguage = "text"\nsource = "synthetic"\n'
        + toml_extra + checks
    )
    return load_task(task_dir)


def write_call(path="notes.txt", content="hello\n", id="c1"):
    return ModelReply(
        tool_calls=[ToolCall(id, "write_file", json.dumps({"path": path, "content": content}))],
        prompt_tokens=10, completion_tokens=5,
    )


def shell_call(command, id="s1"):
    return ModelReply(tool_calls=[ToolCall(id, "run_shell", json.dumps({"command": command}))])


def scripted(*replies):
    return lambda profile: FakeClient(list(replies))


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "run"
    (path / "traces").mkdir(parents=True)
    return path


def lines(run_dir):
    return [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines()]


def test_a_passing_run_is_recorded(tmp_path, run_dir):
    task = make_task(tmp_path)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False,
                      client_factory=scripted(write_call(), ModelReply(content="Created notes.txt.")))
    assert result.passed and result.reason == "done"
    assert result.steps == 2 and result.tool_calls == 1 and result.tool_errors == 0
    assert result.changes == [{"path": "notes.txt", "kind": "created", "added": 1, "removed": 0}]
    assert result.checks == [{"type": "file_exists", "passed": True, "reason": "notes.txt exists"}]
    [line] = lines(run_dir)
    assert line["task"] == "make-notes" and line["passed"] is True and line["profile"] == "fake"
    assert Path(line["trace"]).is_file() and Path(line["trace"]).parent == run_dir / "traces"


def test_a_failing_check_fails_the_run_with_its_reason(tmp_path, run_dir):
    task = make_task(tmp_path)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False,
                      client_factory=scripted(ModelReply(content="I'll create it later."), ModelReply(content="Done.")))
    assert not result.passed and result.reason == "done"
    assert result.checks[0]["reason"] == "notes.txt does not exist"
    assert result.nudges == 1  # "I'll create it later" announced a step without taking it


def test_the_answer_is_checked(tmp_path, run_dir):
    checks = '[[checks]]\ntype = "answer_mentions"\npatterns = ["demo", "readme"]\n'
    task = make_task(tmp_path, checks=checks)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False,
                      client_factory=scripted(ModelReply(content="This is a demo project with a README.")))
    assert result.passed


def test_a_result_is_written_when_the_model_errors(tmp_path, run_dir):
    class Down:
        profile = PROFILE

        def complete(self, *a, **k):
            raise ModelError("Cannot reach the server")

        assistant_message = FakeClient.assistant_message

    task = make_task(tmp_path)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False, client_factory=lambda p: Down())
    assert not result.passed and result.reason == "error" and "Cannot reach the server" in result.error
    assert lines(run_dir)[0]["reason"] == "error"


def test_an_unexpected_exception_is_recorded(tmp_path, run_dir):
    class Broken(FakeClient):
        def complete(self, *a, **k):
            raise RuntimeError("bug in a client")

    task = make_task(tmp_path)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False, client_factory=lambda p: Broken([]))
    assert not result.passed and "RuntimeError: bug in a client" in result.error


def test_a_timeout_ends_the_run_and_counts_as_a_failure(tmp_path, run_dir):
    class Slow(FakeClient):
        def complete(self, *a, **k):
            time.sleep(10)

    task = make_task(tmp_path, toml_extra="timeout = 1\n")
    started = time.monotonic()
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False, client_factory=lambda p: Slow([]))
    assert time.monotonic() - started < 5
    assert not result.passed and result.reason == "timeout"


def test_a_broken_setup_is_recorded(tmp_path, run_dir):
    task = make_task(tmp_path)
    (task.dir / "setup.sh").write_text("exit 7\n")
    toml = (task.dir / "task.toml").read_text().replace("[[checks]]", '[workspace]\nsetup = "setup.sh"\n\n[[checks]]', 1)
    (task.dir / "task.toml").write_text(toml)
    task = load_task(task.dir)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=False, client_factory=scripted())
    assert result.reason == "setup_failed" and "setup script" in result.error


def test_without_a_sandbox_the_runner_refuses(tmp_path, run_dir, monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: False)
    task = make_task(tmp_path)
    result = run_task(task, PROFILE, run_dir=run_dir, use_sandbox=True, client_factory=scripted(write_call()))
    assert not result.passed and result.reason == "setup_failed" and "only on macOS" in result.error


def test_run_suite_repeats_and_records_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    task = make_task(tmp_path)
    run_dir, results = run_suite(
        [task], [PROFILE], repeats=2, use_sandbox=False,
        client_factory=lambda p: FakeClient([write_call(), ModelReply(content="done")]),
    )
    assert [r.repeat for r in results] == [1, 2] and all(r.passed for r in results)
    assert len(lines(run_dir)) == 2
    meta = json.loads((run_dir / "run.json").read_text())
    assert meta["tasks"] == ["make-notes"] and meta["repeats"] == 2


@pytest.mark.skipif(not sandbox.available(), reason="sandbox-exec exists only on macOS")
def test_the_sandbox_blocks_writes_outside_the_workspace(tmp_path, run_dir):
    outside = Path.home() / f".beastron-sandbox-probe-{uuid.uuid4().hex[:8]}"
    task = make_task(tmp_path, checks='[[checks]]\ntype = "file_exists"\npath = "inside.txt"\n')
    try:
        result = run_task(
            task, PROFILE, run_dir=run_dir, use_sandbox=True,
            client_factory=scripted(
                shell_call(f"echo x > {outside}; echo inside > inside.txt"),
                ModelReply(content="done"),
            ),
        )
        assert not outside.exists()  # blocked ...
        assert result.passed  # ... while writing inside the workspace works
        trace = [json.loads(l) for l in Path(result.trace).read_text().splitlines()]
        shell = next(r for r in trace if r["type"] == "tool_end")
        assert "Operation not permitted" in shell["output"]  # and the attempt is recorded
    finally:
        outside.unlink(missing_ok=True)
