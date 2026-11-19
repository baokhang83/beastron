import json

import pytest
from typer.testing import CliRunner

from firox.cli import app
from firox.evals.report import (
    compare,
    history,
    load_baseline,
    load_summary,
    save_baseline,
    save_summary,
    summarize,
    wilson,
)


def result(task, passed, *, category="fix", seconds=10.0, reason=None, digest="d1", profile="fast", commit="abc1234def"):
    return {
        "task": task, "category": category, "profile": profile, "model": "qwen3:8b", "passed": passed,
        "reason": reason or ("done" if passed else "done"), "seconds": seconds, "nudges": 1, "tool_errors": 0,
        "model_digest": digest, "firox_commit": commit, "sandboxed": True,
    }


def runs(task, pattern, **kw):
    """runs("fix-a", "PPF") -> three runs of fix-a: pass, pass, fail."""
    return [result(task, ch == "P", **kw) for ch in pattern]


def one_summary(results, run_id="20261119T060000Z_abc"):
    [summary] = summarize(results, run_id=run_id)
    return summary


# ----- summaries ----------------------------------------------------------------

def test_summary_per_task_category_and_totals():
    s = one_summary(runs("fix-a", "PPF", seconds=10) + runs("fix-b", "FFF", seconds=30)
                    + runs("explain-a", "PPP", category="explain", seconds=20))
    assert s["tasks"]["fix-a"] == {"category": "fix", "runs": 3, "passed": 2, "timeouts": 0, "errors": 0,
                                   "median_seconds": 10.0}
    assert s["categories"]["fix"] == {"tasks": 2, "runs": 6, "passed": 2, "rate": 0.3333}
    assert s["categories"]["explain"]["rate"] == 1.0
    t = s["totals"]
    assert (t["runs"], t["passed"]) == (9, 5)
    assert t["score"] == pytest.approx((1 / 3 + 1.0) / 2, abs=1e-4)  # plain average of the categories
    assert (t["median_seconds"], t["p90_seconds"]) == (20.0, 30.0)
    assert s["model_digests"] == ["d1"] and s["firox_commit"] == "abc1234def"


def test_timeouts_and_errors_are_counted():
    s = one_summary([result("a", False, reason="timeout"), result("a", False, reason="error"),
                     result("a", False, reason="setup_failed"), result("a", True)])
    assert s["tasks"]["a"]["timeouts"] == 1 and s["tasks"]["a"]["errors"] == 2
    assert (s["totals"]["timeouts"], s["totals"]["errors"]) == (1, 2)


def test_one_summary_per_profile():
    summaries = summarize(runs("a", "PP", profile="fast") + runs("a", "FF", profile="thinking"))
    assert sorted(s["profile"] for s in summaries) == ["fast", "thinking"]


def test_summaries_and_baselines_are_saved_and_loaded(tmp_path):
    s = one_summary(runs("a", "PPP"))
    path = save_summary(s, tmp_path / "results")
    assert path.name == "20261119T060000Z_fast_abc1234.json"
    assert load_summary(path) == s
    save_baseline(s, tmp_path / "baselines")
    assert load_baseline("fast", tmp_path / "baselines") == s
    assert load_baseline("other", tmp_path / "baselines") is None


def test_history_is_oldest_first(tmp_path):
    save_summary(one_summary(runs("a", "FFF"), run_id="20261118T010000Z_x"), tmp_path)
    save_summary(one_summary(runs("a", "PPP"), run_id="20261119T010000Z_y"), tmp_path)
    assert [s["totals"]["passed"] for s in history("fast", tmp_path)] == [0, 3]


# ----- regressions -------------------------------------------------------------------

def test_a_task_that_now_fails_2_of_3_is_a_regression():
    base = one_summary(runs("a", "PPP"))
    cmp = compare(one_summary(runs("a", "PFF")), base)
    assert cmp.task_regressions == ["a"] and cmp.has_regressions
    assert "passed 3/3 in the baseline, now 1/3" in cmp.notes[0]


def test_one_failure_in_three_is_noise_not_a_regression():
    base = one_summary(runs("a", "PPP"))
    cmp = compare(one_summary(runs("a", "PPF")), base)
    assert cmp.task_regressions == [] and not cmp.has_regressions


def test_improvements_added_and_removed_tasks():
    base = one_summary(runs("a", "FFF") + runs("gone", "PPP"))
    cmp = compare(one_summary(runs("a", "PPP") + runs("new", "PPP")), base)
    assert cmp.task_improvements == ["a"] and cmp.added == ["new"] and cmp.removed == ["gone"]
    assert not cmp.has_regressions


def test_a_category_drop_beyond_the_confidence_interval():
    base = one_summary([r for i in range(10) for r in runs(f"t{i}", "PPP")])  # 30/30
    worse = one_summary([r for i in range(10) for r in runs(f"t{i}", "PPP" if i < 6 else "PPF")])  # 26/30: noise
    much_worse = one_summary([r for i in range(10) for r in runs(f"t{i}", "PPF" if i < 6 else "FFP")])  # 16/30
    assert compare(worse, base).category_regressions == []
    assert compare(much_worse, base).category_regressions == ["fix"]


def test_a_changed_model_is_reported():
    base = one_summary(runs("a", "PPP", digest="d1"))
    cmp = compare(one_summary(runs("a", "PPP", digest="d2")), base)
    assert cmp.digest_changed and not cmp.has_regressions
    assert "the model changed since the baseline" in cmp.notes[0]


def test_wilson_interval():
    low, high = wilson(0, 3)
    assert low == 0.0 and high == pytest.approx(0.5615, abs=1e-3)
    low, high = wilson(30, 30)
    assert low == pytest.approx(0.886, abs=1e-3) and high == 1.0


# ----- commands ------------------------------------------------------------------------

def write_run(state, name, results):
    run = state / "evals" / "runs" / name
    run.mkdir(parents=True)
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))
    return run


def test_baseline_then_report_flags_a_regression(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("FIROX_STATE_DIR", str(state))
    baselines, results = tmp_path / "baselines", tmp_path / "results"
    write_run(state, "20261118T010000Z_aaaaaa", runs("fix-a", "PPP") + runs("fix-b", "PPP"))
    cli = CliRunner()

    stored = cli.invoke(app, ["eval", "baseline", "--baselines-dir", str(baselines)])
    assert stored.exit_code == 0 and (baselines / "fast.json").is_file()

    same = cli.invoke(app, ["eval", "report", "--baselines-dir", str(baselines)])
    assert same.exit_code == 0 and "no regressions against the baseline" in same.output

    write_run(state, "20261119T010000Z_bbbbbb", runs("fix-a", "PPP") + runs("fix-b", "FFP"))
    worse = cli.invoke(app, ["eval", "report", "--baselines-dir", str(baselines)])  # the latest run
    assert worse.exit_code == 1
    assert "REGRESSIONS FOUND" in worse.output and "fix-b" in worse.output and "regressed" in worse.output


def test_report_history(tmp_path):
    results = tmp_path / "results"
    save_summary(one_summary(runs("a", "FFF"), run_id="20261118T010000Z_x"), results)
    save_summary(one_summary(runs("a", "PPP"), run_id="20261119T010000Z_y"), results)
    out = CliRunner().invoke(app, ["eval", "report", "--history", "--results-dir", str(results)])
    assert out.exit_code == 0
    assert out.output.index("20261118T010000Z") < out.output.index("20261119T010000Z")
    assert "0%" in out.output and "100%" in out.output
