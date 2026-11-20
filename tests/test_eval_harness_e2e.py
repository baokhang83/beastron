"""The eval harness end to end, as CI runs it: real seed tasks, a scripted model, no server, no network.

A scripted pass and a scripted fail must produce the expected report, and a
run that turns a pass into a fail must be flagged as a regression, both
through the library and through the `beastron eval` commands.
"""

import functools
from pathlib import Path

import pytest
from typer.testing import CliRunner

import beastron.evals.runner as runner
from beastron.cli import app
from beastron.evals.report import compare, summarize
from beastron.evals.task import load_tasks
from scripted import EDIT_THE_WRONG_LINE, FIX_OFF_BY_ONE, PROFILE, SET_THE_RETRY_DELAY, factory

TASKS = Path(__file__).resolve().parents[1] / "evals" / "tasks"
IDS = ["fix-python-off-by-one", "edit-exact-indentation"]
GOOD = {"tests in test_utils.py fail": FIX_OFF_BY_ONE, "retried after 5 seconds": SET_THE_RETRY_DELAY}
WORSE = {"tests in test_utils.py fail": FIX_OFF_BY_ONE, "retried after 5 seconds": EDIT_THE_WRONG_LINE}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))


def suite(scripts, repeats=3):
    tasks = load_tasks(TASKS, ids=IDS)
    return runner.run_suite(tasks, [PROFILE], repeats=repeats, use_sandbox=False, client_factory=factory(scripts))


def test_a_scripted_pass_and_a_scripted_fail_produce_the_expected_summary():
    run_dir, results = suite(WORSE)
    assert len(results) == 6 and (run_dir / "results.jsonl").is_file()

    [summary] = summarize([r.__dict__ for r in results], run_id=run_dir.name)
    tasks = summary["tasks"]
    assert (tasks["fix-python-off-by-one"]["passed"], tasks["fix-python-off-by-one"]["runs"]) == (3, 3)
    assert (tasks["edit-exact-indentation"]["passed"], tasks["edit-exact-indentation"]["runs"]) == (0, 3)
    assert summary["categories"]["fix"]["rate"] == 1.0
    assert summary["categories"]["config"]["rate"] == 0.0
    assert summary["totals"]["score"] == 0.5 and summary["totals"]["errors"] == 0

    wrong = next(r for r in results if r.task == "edit-exact-indentation")
    assert not wrong.passed and not wrong.checks[0]["passed"]  # the retry delay was never set to 5
    assert wrong.changes == [{"path": "handlers.py", "kind": "modified", "added": 1, "removed": 1}]


def test_a_pass_turning_into_a_fail_is_a_regression():
    _, good = suite(GOOD)
    _, worse = suite(WORSE)
    [baseline] = summarize([r.__dict__ for r in good])
    [current] = summarize([r.__dict__ for r in worse])
    assert baseline["totals"]["passed"] == 6
    comparison = compare(current, baseline)
    assert comparison.task_regressions == ["edit-exact-indentation"] and comparison.has_regressions


def test_the_eval_commands_end_to_end(tmp_path, monkeypatch):
    """`beastron eval run`, `baseline` and `report`, with the scripted model in place of a server."""
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
    assert "REGRESSIONS FOUND" in second.output and "edit-exact-indentation" in second.output

    report = cli.invoke(app, ["eval", "report", "--baselines-dir", str(baselines_dir)])  # the latest run again
    assert report.exit_code == 1 and "regressed" in report.output
