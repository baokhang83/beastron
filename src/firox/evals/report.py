"""Summaries, baselines and regression detection for eval runs.

- A **summary** condenses one run (one model profile) into pass counts per
  task and category, timing, and what was used: the model digest and firox's
  commit. It holds no transcripts, so it can be committed to `evals/results/`;
  the committed summaries are the score's history.
- A **baseline** is a summary stored as the reference for a profile, in
  `evals/baselines/<profile>.json`.
- **Comparing** a summary with its baseline flags:
  - a task regression: the task passed in most baseline runs, and now fails in
    at least 2 of every 3 runs
  - a category regression: the category's pass rate is clearly lower, meaning
    the 95% confidence intervals (Wilson score) of the two rates don't overlap;
    both come from few runs, so smaller drops are treated as noise
  - a changed model digest, so a model update can't hide inside a score change
  - improvements, and tasks added or removed

The score weights each category's pass rate by how often the owner asks for that
kind of task (`evals/usecases.json`, from `firox usecases classify --write`).
Categories without a weight, or no weights file at all, fall back to the plain
average of the category pass rates.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
RESULTS_DIR = Path("evals/results")
BASELINES_DIR = Path("evals/baselines")
WEIGHTS_FILE = Path("evals/usecases.json")
OVERRIDES_FILE = Path("evals/weights.toml")  # the owner's adjustments; never overwritten
Z95 = 1.96


# ----- reading runs -----------------------------------------------------------

def load_results(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "results.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"no results in {run_dir}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_run(runs_dir: Path) -> Path:
    """The run whose results were written last.

    Run folders are named by start second plus a random suffix, so two runs
    started in the same second can't be ordered by name.
    """
    runs = [p for p in runs_dir.glob("*") if (p / "results.jsonl").is_file()]
    if not runs:
        raise FileNotFoundError(f"no eval runs in {runs_dir}")
    return max(runs, key=lambda p: ((p / "results.jsonl").stat().st_mtime_ns, p.name))


# ----- summaries ----------------------------------------------------------------

def load_weights(path: Path = WEIGHTS_FILE, overrides: Path = OVERRIDES_FILE) -> dict[str, float] | None:
    """Category weights: measured from real usage, then adjusted by the owner's overrides. None if neither exists."""
    measured: dict[str, float] = {}
    if path.is_file():
        measured = {c: float(w) for c, w in (json.loads(path.read_text(encoding="utf-8")).get("weights") or {}).items()}
    adjusted = apply_overrides(measured, load_overrides(overrides))
    return adjusted or None


def load_overrides(path: Path = OVERRIDES_FILE) -> dict[str, dict[str, float]]:
    """The owner's adjustments (evals/weights.toml):

        [multiply]          # scale a measured weight, e.g. count fixes 1.5 times as much
        fix = 1.5
        [set]               # or fix a category's share of the score (0 to 1); the others share the rest
        explain = 0.20
    """
    if not path.is_file():
        return {"multiply": {}, "set": {}}
    import tomllib
    from .task import CATEGORIES

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown_tables = set(data) - {"multiply", "set"}
    if unknown_tables:
        raise ValueError(f"{path}: unknown tables {sorted(unknown_tables)}; use [multiply] and [set]")
    result = {"multiply": {}, "set": {}}
    for table in ("multiply", "set"):
        for category, value in (data.get(table) or {}).items():
            if category not in CATEGORIES:
                raise ValueError(f"{path}: [{table}] {category} is not a category; use one of {list(CATEGORIES)}")
            if not isinstance(value, (int, float)) or value < 0 or (table == "set" and value > 1):
                raise ValueError(f"{path}: [{table}] {category} = {value!r} is out of range")
            result[table][category] = float(value)
    if sum(result["set"].values()) > 1:
        raise ValueError(f"{path}: the [set] shares add up to more than 1")
    return result


def apply_overrides(weights: dict[str, float], overrides: dict[str, dict[str, float]]) -> dict[str, float]:
    multiplied = {c: w * overrides["multiply"].get(c, 1.0) for c, w in weights.items()}
    fixed = overrides["set"]
    rest = {c: w for c, w in multiplied.items() if c not in fixed and w > 0}
    remaining = 1.0 - sum(fixed.values())
    total = sum(rest.values())
    result = {c: w / total * remaining for c, w in rest.items()} if total else {}
    result.update(fixed)
    return {c: round(w, 4) for c, w in sorted(result.items(), key=lambda kv: -kv[1]) if w > 0}


def score(categories: dict[str, dict[str, Any]], weights: dict[str, float] | None) -> tuple[float, str]:
    """(score, how it was weighted): usage-weighted where weights exist for the categories run."""
    rates = {c: v["rate"] for c, v in categories.items()}
    used = {c: weights[c] for c in rates if weights and weights.get(c, 0) > 0}
    if used:
        return sum(rates[c] * w for c, w in used.items()) / sum(used.values()), "usage"
    return statistics.mean(rates.values()), "equal"


def summarize(results: list[dict[str, Any]], *, run_id: str = "", weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """One summary per model profile found in the results."""
    by_profile: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_profile.setdefault(r["profile"], []).append(r)
    return [_summary(profile, runs, run_id, weights) for profile, runs in by_profile.items()]


def _summary(profile: str, runs: list[dict[str, Any]], run_id: str, weights: dict[str, float] | None = None) -> dict[str, Any]:
    tasks: dict[str, dict[str, Any]] = {}
    for r in runs:
        t = tasks.setdefault(r["task"], {"category": r["category"], "runs": 0, "passed": 0, "timeouts": 0,
                                         "errors": 0, "seconds": []})
        t["runs"] += 1
        t["passed"] += bool(r["passed"])
        t["timeouts"] += r["reason"] == "timeout"
        t["errors"] += r["reason"] in ("error", "setup_failed")
        t["seconds"].append(r["seconds"])
    for t in tasks.values():
        t["median_seconds"] = round(statistics.median(t.pop("seconds")), 1)

    categories: dict[str, dict[str, Any]] = {}
    for task in tasks.values():
        c = categories.setdefault(task["category"], {"tasks": 0, "runs": 0, "passed": 0})
        c["tasks"] += 1
        c["runs"] += task["runs"]
        c["passed"] += task["passed"]
    for c in categories.values():
        c["rate"] = round(c["passed"] / c["runs"], 4)

    seconds = sorted(r["seconds"] for r in runs)
    passed = sum(bool(r["passed"]) for r in runs)
    total_score, weighting = score(categories, weights)
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "profile": profile,
        "model": runs[0]["model"],
        "model_digests": sorted({r["model_digest"] for r in runs if r.get("model_digest")}),
        "firox_commit": runs[0].get("firox_commit"),
        "sandboxed": all(r.get("sandboxed", True) for r in runs),
        "tasks": dict(sorted(tasks.items())),
        "categories": dict(sorted(categories.items())),
        "totals": {
            "runs": len(runs),
            "passed": passed,
            "rate": round(passed / len(runs), 4),
            "score": round(total_score, 4),
            "score_weighting": weighting,  # "usage" (from evals/usecases.json) or "equal"
            "median_seconds": round(statistics.median(seconds), 1),
            "p90_seconds": round(_percentile(seconds, 0.9), 1),
            "timeouts": sum(r["reason"] == "timeout" for r in runs),
            "errors": sum(r["reason"] in ("error", "setup_failed") for r in runs),
            "nudges": sum(r.get("nudges", 0) for r in runs),
            "tool_errors": sum(r.get("tool_errors", 0) for r in runs),
        },
    }


def save_summary(summary: dict[str, Any], results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary["run_id"].split("_")[0] or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    commit = (summary.get("firox_commit") or "nocommit")[:7]
    path = results_dir / f"{stamp}_{_safe(summary['profile'])}_{commit}.json"
    _write(path, summary)
    return path


def load_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unsupported summary schema {data.get('schema')!r}")
    return data


def history(profile: str, results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    """Committed summaries for a profile, oldest first."""
    found = [load_summary(p) for p in sorted(results_dir.glob(f"*_{_safe(profile)}_*.json"))]
    return [s for s in found if s["profile"] == profile]


# ----- baselines ------------------------------------------------------------------

def baseline_path(profile: str, baselines_dir: Path = BASELINES_DIR) -> Path:
    return baselines_dir / f"{_safe(profile)}.json"


def save_baseline(summary: dict[str, Any], baselines_dir: Path = BASELINES_DIR) -> Path:
    baselines_dir.mkdir(parents=True, exist_ok=True)
    path = baseline_path(summary["profile"], baselines_dir)
    _write(path, summary)
    return path


def load_baseline(profile: str, baselines_dir: Path = BASELINES_DIR) -> dict[str, Any] | None:
    path = baseline_path(profile, baselines_dir)
    return load_summary(path) if path.is_file() else None


# ----- comparison -------------------------------------------------------------------

@dataclass
class Comparison:
    task_regressions: list[str] = field(default_factory=list)
    task_improvements: list[str] = field(default_factory=list)
    category_regressions: list[str] = field(default_factory=list)
    digest_changed: bool = False
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.task_regressions or self.category_regressions)


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> Comparison:
    result = Comparison()
    now, before = current["tasks"], baseline["tasks"]
    result.added = sorted(set(now) - set(before))
    result.removed = sorted(set(before) - set(now))

    for task in sorted(set(now) & set(before)):
        b, c = before[task], now[task]
        b_rate = b["passed"] / b["runs"]
        c_fails = c["runs"] - c["passed"]
        if b_rate > 0.5 and c_fails * 3 >= c["runs"] * 2:
            result.task_regressions.append(task)
            result.notes.append(f"{task}: passed {b['passed']}/{b['runs']} in the baseline, now {c['passed']}/{c['runs']}")
        elif b_rate < 0.5 and c["passed"] * 3 >= c["runs"] * 2:
            result.task_improvements.append(task)
            result.notes.append(f"{task}: improved from {b['passed']}/{b['runs']} to {c['passed']}/{c['runs']}")

    for category in sorted(set(current["categories"]) & set(baseline["categories"])):
        b, c = baseline["categories"][category], current["categories"][category]
        # Both rates come from a few runs, so both are uncertain: only call it a
        # regression when the two 95% ranges don't even overlap.
        base_low, _ = wilson(b["passed"], b["runs"])
        _, now_high = wilson(c["passed"], c["runs"])
        if now_high < base_low:
            result.category_regressions.append(category)
            result.notes.append(
                f"category {category}: {c['rate']:.0%} ({c['passed']}/{c['runs']}) is clearly below the baseline's "
                f"{b['rate']:.0%} ({b['passed']}/{b['runs']})"
            )

    if set(current.get("model_digests", [])) != set(baseline.get("model_digests", [])):
        result.digest_changed = True
        result.notes.append(
            f"the model changed since the baseline: {', '.join(d[:12] for d in baseline.get('model_digests', [])) or 'unknown'}"
            f" → {', '.join(d[:12] for d in current.get('model_digests', [])) or 'unknown'}"
        )
    return result


def wilson(passed: int, runs: int, z: float = Z95) -> tuple[float, float]:
    """95% confidence interval for a pass rate (Wilson score), robust for small run counts."""
    if runs == 0:
        return 0.0, 1.0
    p = passed / runs
    denominator = 1 + z * z / runs
    centre = (p + z * z / (2 * runs)) / denominator
    margin = z * math.sqrt(p * (1 - p) / runs + z * z / (4 * runs * runs)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


# ----- helpers ------------------------------------------------------------------------

def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    index = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[index]


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in name)


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
