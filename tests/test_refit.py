"""Owner weight overrides, the coverage report, and scanning recent history only."""

from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from beastron.cli import app
from beastron.evals.report import apply_overrides, load_overrides, load_weights
from beastron.usecases.commands import parse_since
from beastron.usecases.requests import load

MEASURED = {"explain": 0.5, "fix": 0.3, "git": 0.2}


def test_no_overrides_keeps_the_measured_weights(tmp_path):
    assert apply_overrides(MEASURED, load_overrides(tmp_path / "missing.toml")) == MEASURED


def test_multiply_scales_and_renormalizes():
    weights = apply_overrides(MEASURED, {"multiply": {"fix": 2.0}, "set": {}})
    # fix 0.3 x 2 = 0.6; total 1.3
    assert weights == {"fix": round(0.6 / 1.3, 4), "explain": round(0.5 / 1.3, 4), "git": round(0.2 / 1.3, 4)}
    assert list(weights) == ["fix", "explain", "git"]  # largest first


def test_set_fixes_a_share_and_the_others_share_the_rest():
    weights = apply_overrides(MEASURED, {"multiply": {}, "set": {"git": 0.5}})
    assert weights["git"] == 0.5
    assert weights["explain"] == pytest.approx(0.5 * 0.5 / 0.8, abs=1e-4)
    assert weights["fix"] == pytest.approx(0.5 * 0.3 / 0.8, abs=1e-4)


def test_overrides_file_is_validated(tmp_path):
    bad = tmp_path / "weights.toml"
    for content, message in [
        ("[multiply]\nmagic = 2\n", "not a category"),
        ("[set]\nfix = 1.5\n", "out of range"),
        ("[set]\nfix = 0.6\nexplain = 0.6\n", "more than 1"),
        ("[boost]\nfix = 2\n", "unknown tables"),
    ]:
        bad.write_text(content)
        with pytest.raises(ValueError, match=message):
            load_overrides(bad)


def test_load_weights_applies_the_overrides_file(tmp_path):
    measured = tmp_path / "usecases.json"
    measured.write_text('{"weights": {"explain": 0.5, "fix": 0.3, "git": 0.2}}')
    overrides = tmp_path / "weights.toml"
    overrides.write_text("[set]\nfix = 0.6\n")
    weights = load_weights(measured, overrides)
    assert weights["fix"] == 0.6 and sum(weights.values()) == pytest.approx(1.0, abs=1e-3)


def test_coverage_reports_gaps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # evals/usecases.json and evals/weights.toml are read from here
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "usecases.json").write_text(
        '{"weights": {"explain": 0.6, "fix": 0.4}, "languages": {"java": 9, "python": 1}}')
    tasks = tmp_path / "tasks"
    for i, category in enumerate(["fix", "fix", "fix", "explain"]):
        d = tasks / f"t{i}"
        (d / "repo").mkdir(parents=True)
        (d / "task.toml").write_text(f'prompt = "x"\ncategory = "{category}"\nlanguage = "python"\nsource = "synthetic"\n'
                                     '[[checks]]\ntype = "no_changes"\n')
    result = CliRunner().invoke(app, ["eval", "coverage", "--tasks-dir", str(tasks), "--size", "10"])
    assert result.exit_code == 0, result.output
    assert "explain +5" in result.output  # 60% of 10 = 6, has 1
    assert "feature +2" in result.output  # every category needs at least 2
    assert "java 90% of your work, 0% of the suite" in result.output


def test_since(tmp_path, monkeypatch):
    assert parse_since("2026-06-01") == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert datetime.now(timezone.utc) - parse_since("2w") > timedelta(days=13)
    with pytest.raises(ValueError, match="number of days or weeks"):
        parse_since("soon")

    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BEASTRON_CODEX_DIR", str(tmp_path / "none"))
    root = tmp_path / "claude"
    (root / "-work").mkdir(parents=True)
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    old = "2020-01-01T00:00:00Z"
    (root / "-work" / "s.jsonl").write_text(
        f'{{"type": "user", "message": {{"content": "old request"}}, "timestamp": "{old}"}}\n'
        f'{{"type": "user", "message": {{"content": "recent request"}}, "timestamp": "{recent}"}}\n')
    monkeypatch.setenv("BEASTRON_CLAUDE_DIR", str(root))
    result = CliRunner().invoke(app, ["usecases", "scan", "--since", "30d"])
    assert result.exit_code == 0, result.output
    assert [r.text for r in load(tmp_path / "state" / "usecases" / "claude.jsonl")] == ["recent request"]
