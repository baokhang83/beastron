"""The eval harness end to end, as CI runs it: real seed tasks, a scripted model, no server, no network.

A scripted pass and a scripted fail must produce the expected report, and a
run that turns a pass into a fail must be flagged as a regression, both
through the library and through the `firox eval` commands.
"""

import functools
from pathlib import Path

import pytest
from typer.testing import CliRunner

import firox.evals.runner as runner
from firox.cli import app
from firox.evals.report import compare, summarize
from firox.evals.task import load_tasks
from scripted import ANSWER_WITHOUT_EDITING, EDIT_INSTEAD_OF_ANSWERING, FIX_OFF_BY_ONE, PROFILE, factory

TASKS = Path(__file__).resolve().parents[1] / "evals" / "tasks"
IDS = ["fix-python-off-by-one", "question-must-not-edit"]
GOOD = {"tests in test_utils.py fail": FIX_OFF_BY_ONE, "What would it take": ANSWER_WITHOUT_EDITING}
WORSE = {"tests in test_utils.py fail": FIX_OFF_BY_ONE, "What would it take": EDIT_INSTEAD_OF_ANSWERING}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIROX_STATE_DIR", str(tmp_path / "state"))


def suite(scripts, repeats=3):
    tasks = load_tasks(TASKS, ids=IDS)
    return runner.run_suite(tasks, [PROFILE], repeats=repeats, use_sandbox=False, client_factory=factory(scripts))


def test_a_scripted_pass_and_a_scripted_fail_produce_the_expected_summary():
    run_dir, results = suite(WORSE)
    assert len(results) == 6 and (run_dir / "results.jsonl").is_file()

    [summary] = summarize([r.__dict__ for r in results], run_id=run_dir.name)
    tasks = summary["tasks"]
    assert (tasks["fix-python-off-by-one"]["passed"], tasks["fix-python-off-by-one"]["runs"]) == (3, 3)
    assert (tasks["question-must-not-edit"]["passed"], tasks["question-must-not-edit"]["runs"]) == (0, 3)
    assert summary["categories"]["fix"]["rate"] == 1.0
    assert summary["categories"]["explain"]["rate"] == 0.0
    assert summary["totals"]["score"] == 0.5 and summary["totals"]["errors"] == 0

    question = next(r for r in results if r.task == "question-must-not-edit")
    assert question.checks[0] == {"type": "no_changes", "passed": False,
                                  "reason": "files changed although none may: todo.py"}
    assert question.changes == [{"path": "todo.py", "kind": "modified", "added": 1, "removed": 0}]  # one line inserted


def test_a_pass_turning_into_a_fail_is_a_regression():
    _, good = suite(GOOD)
    _, worse = suite(WORSE)
    [baseline] = summarize([r.__dict__ for r in good])
    [current] = summarize([r.__dict__ for r in worse])
    assert baseline["totals"]["passed"] == 6
    comparison = compare(current, baseline)
    assert comparison.task_regressions == ["question-must-not-edit"] and comparison.has_regressions


def test_the_eval_commands_end_to_end(tmp_path, monkeypatch):
    """`firox eval run`, `baseline` and `report`, with the scripted model in place of a server."""
    results_dir, baselines_dir = tmp_path / "results", tmp_path / "baselines"
    models = tmp_path / "models.toml"
    models.write_text('[models.scripted]\nbase_url = "http://unused.invalid/v1"\nmodel = "scripted-model"\n')
    common = ["--tasks-dir", str(TASKS), "--config", str(models), "--unsafe-no-sandbox",
              "--results-dir", str(results_dir), "--baselines-dir", str(baselines_dir)]
    cli = CliRunner()

    def run_with(scripts):
        monkeypatch.setattr(runner, "run_suite", functools.partial(original_run_suite, client_factory=factory(scripts)))
        args = ["eval", "run", *common, "-n", "3"]
        for task in IDS:
            args += ["-t", task]
        return cli.invoke(app, args)

    original_run_suite = runner.run_suite
    first = run_with(GOOD)
    assert first.exit_code == 0, first.output
    assert "6/6 runs passed" in first.output and "no baseline yet" in first.output
    assert len(list(results_dir.glob("*_scripted_*.json"))) == 1  # the summary was written

    stored = cli.invoke(app, ["eval", "baseline", "--baselines-dir", str(baselines_dir)])
    assert stored.exit_code == 0 and (baselines_dir / "scripted.json").is_file()

    second = run_with(WORSE)
    assert second.exit_code == 1  # a regression fails the command
    assert "3/6 runs passed" in second.output
    assert "REGRESSIONS FOUND" in second.output and "question-must-not-edit" in second.output

    report = cli.invoke(app, ["eval", "report", "--baselines-dir", str(baselines_dir)])  # the latest run again
    assert report.exit_code == 1 and "regressed" in report.output
