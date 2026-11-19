"""`firox eval …` commands.

- `run`: run tasks, write a summary per model profile, compare with the baseline
- `report`: summarize a run (default: the latest) or show the history
- `baseline`: store a run as the reference for its profile(s)

`run` and `report` exit with status 1 when they find a regression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from dataclasses import asdict

from rich.table import Table

from ..config import ConfigError, load_config
from ..trace import state_dir
from ..ui import RichUI
from . import runner, sandbox
from .report import (
    BASELINES_DIR,
    RESULTS_DIR,
    Comparison,
    compare,
    history,
    latest_run,
    load_baseline,
    load_results,
    load_summary,
    load_weights,
    save_baseline,
    save_summary,
    summarize,
)
from .runner import RunResult
from .task import TASKS_DIR, TaskError, load_tasks

eval_app = typer.Typer(
    name="eval",
    help="Measure firox on the eval suite (see docs/plan.md).",
    add_completion=False,
    no_args_is_help=True,
)

@eval_app.command("run")
def run_suite(
    suite: Annotated[str | None, typer.Option("--suite", "-s", help="Only tasks with this tag, e.g. seed or quick.")] = None,
    task: Annotated[list[str] | None, typer.Option("--task", "-t", help="Only this task id (repeatable).")] = None,
    models: Annotated[str | None, typer.Option("--models", "-m", help="Comma-separated model profiles (default: the config's default).")] = None,
    repeats: Annotated[int, typer.Option("--repeats", "-n", min=1, help="Runs per task and profile.")] = 3,
    tasks_dir: Annotated[Path, typer.Option(help="Folder with the eval tasks.")] = TASKS_DIR,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Path to models.toml.")] = None,
    keep: Annotated[bool, typer.Option(help="Keep each run's workspace for inspection.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show the agent's work as it happens.")] = False,
    unsafe_no_sandbox: Annotated[
        bool, typer.Option("--unsafe-no-sandbox", help="Run shell commands without the sandbox (they're approved automatically!).")
    ] = False,
    save: Annotated[bool, typer.Option(help="Write a summary per profile to the results folder.")] = True,
    results_dir: Annotated[Path, typer.Option(help="Where summaries are written.")] = RESULTS_DIR,
    baselines_dir: Annotated[Path, typer.Option(help="Where baselines are read from.")] = BASELINES_DIR,
) -> None:
    """Run eval tasks for one or more model profiles, then report and compare with the baseline."""
    console = Console(highlight=False)
    try:
        cfg = load_config(config)
        profiles = [cfg.get(name.strip()) for name in models.split(",")] if models else [cfg.get(None)]
        tasks = load_tasks(tasks_dir, tag=suite, ids=task or None)
    except (ConfigError, TaskError) as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from None
    if not tasks:
        console.print("[yellow]No tasks match.[/]")
        raise typer.Exit(1)
    if not unsafe_no_sandbox and not sandbox.available():
        console.print("[red]The sandbox (sandbox-exec) is only available on macOS. Shell commands are approved "
                      "automatically during evals, so firox won't run them unsandboxed unless you pass --unsafe-no-sandbox.[/]")
        raise typer.Exit(1)

    total = len(tasks) * len(profiles) * repeats
    console.print(f"Running {len(tasks)} task(s) × {len(profiles)} profile(s) × {repeats} repeat(s) = {total} runs"
                  + ("  [yellow](no sandbox)[/]" if unsafe_no_sandbox else ""))
    done = 0

    def progress(r: RunResult) -> None:
        nonlocal done
        done += 1
        mark = "[green]PASS[/]" if r.passed else "[red]FAIL[/]"
        failed = [c["reason"].splitlines()[0] for c in r.checks if not c["passed"]]
        why = f"  {r.reason}" if r.reason != "done" else ""
        # escape: reasons often contain brackets, e.g. Maven's [ERROR]
        detail = f"  [dim]{escape(failed[0])}[/]" if failed else (f"  [dim]{escape(r.error)}[/]" if r.error else "")
        console.print(f"[{done:>{len(str(total))}}/{total}] {mark} {r.task} · {r.profile} #{r.repeat} · "
                      f"{r.seconds:.0f}s · {r.steps} steps{why}{detail}")

    ui = RichUI(console=console, verbose=True) if verbose else None
    try:
        run_dir, results = runner.run_suite(
            tasks, profiles, repeats=repeats, progress=progress, use_sandbox=not unsafe_no_sandbox,
            keep=keep, on_event=ui.handle if ui else None,
        )
    except KeyboardInterrupt:
        console.print("[yellow]Stopped. Finished runs are saved.[/]")
        raise typer.Exit(130) from None
    passed = sum(r.passed for r in results)
    console.print(f"\n{passed}/{len(results)} runs passed · raw results: {run_dir / 'results.jsonl'}")
    summaries = summarize([asdict(r) for r in results], run_id=run_dir.name, weights=load_weights())
    regressed = _report(console, summaries, baselines_dir)
    if save:
        for summary in summaries:
            console.print(f"[dim]summary: {save_summary(summary, results_dir)}[/]")
    raise typer.Exit(1 if regressed else 0)


@eval_app.command("report")
def report(
    run: Annotated[Path | None, typer.Argument(help="A run folder (default: the latest run).")] = None,
    show_history: Annotated[bool, typer.Option("--history", help="Show the committed summaries over time instead.")] = False,
    profile: Annotated[str | None, typer.Option("--profile", "-p", help="Only this model profile.")] = None,
    results_dir: Annotated[Path, typer.Option(help="Where committed summaries are.")] = RESULTS_DIR,
    baselines_dir: Annotated[Path, typer.Option(help="Where baselines are read from.")] = BASELINES_DIR,
) -> None:
    """Show results per category and flag regressions against the baseline (exit code 1 if any)."""
    console = Console(highlight=False)
    if show_history:
        _history(console, results_dir, profile)
        return
    summaries = _summaries_of(console, run, profile)
    raise typer.Exit(1 if _report(console, summaries, baselines_dir) else 0)


@eval_app.command("baseline")
def baseline(
    run: Annotated[Path | None, typer.Argument(help="A run folder (default: the latest run).")] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p", help="Only this model profile.")] = None,
    baselines_dir: Annotated[Path, typer.Option(help="Where baselines are written.")] = BASELINES_DIR,
) -> None:
    """Store a run's results as the baseline for its model profile(s)."""
    console = Console(highlight=False)
    for summary in _summaries_of(console, run, profile):
        path = save_baseline(summary, baselines_dir)
        t = summary["totals"]
        console.print(f"baseline for {summary['profile']}: {t['passed']}/{t['runs']} runs passed · {path}")


# ----- rendering ------------------------------------------------------------------

def _summaries_of(console: Console, run: Path | None, profile: str | None) -> list[dict]:
    try:
        run_dir = run or latest_run(state_dir() / "evals" / "runs")
        summaries = summarize(load_results(run_dir), run_id=run_dir.name, weights=load_weights())
    except FileNotFoundError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from None
    if profile:
        summaries = [s for s in summaries if s["profile"] == profile]
        if not summaries:
            console.print(f"[red]no results for profile {escape(profile)} in {run_dir}[/]")
            raise typer.Exit(1)
    return summaries


def _report(console: Console, summaries: list[dict], baselines_dir: Path) -> bool:
    """Print each profile's summary and its comparison with the baseline. True if anything regressed."""
    regressed = False
    for summary in summaries:
        base = load_baseline(summary["profile"], baselines_dir)
        comparison = compare(summary, base) if base else None
        _render(console, summary, base, comparison)
        regressed |= bool(comparison and comparison.has_regressions)
    return regressed


def _render(console: Console, s: dict, base: dict | None, cmp: Comparison | None) -> None:
    t = s["totals"]
    digest = (s["model_digests"] or ["unknown"])[0][:12]
    console.print(f"\n[bold]{escape(s['profile'])}[/] ({escape(s['model'])}, digest {digest}) · "
                  f"firox {(s['firox_commit'] or 'unknown')[:7]}" + ("" if s["sandboxed"] else " · [yellow]not sandboxed[/]"))

    table = Table(box=None, pad_edge=False, show_edge=False)
    for column in ("category", "tasks", "passed", "rate", "baseline"):
        table.add_column(column, justify="left" if column == "category" else "right")
    for name, c in s["categories"].items():
        b = base["categories"].get(name) if base else None
        flag = " [red]▼[/]" if cmp and name in cmp.category_regressions else ""
        table.add_row(name, str(c["tasks"]), f"{c['passed']}/{c['runs']}", f"{c['rate']:.0%}{flag}",
                      f"{b['rate']:.0%}" if b else "-")
    console.print(table)

    tasks = Table(box=None, pad_edge=False, show_edge=False)
    for column in ("task", "passed", "median", "baseline"):
        tasks.add_column(column, justify="left" if column == "task" else "right")
    for name, task in s["tasks"].items():
        b = base["tasks"].get(name) if base else None
        mark = ""
        if cmp and name in cmp.task_regressions:
            mark = " [red]regressed[/]"
        elif cmp and name in cmp.task_improvements:
            mark = " [green]improved[/]"
        elif cmp and name in cmp.added:
            mark = " [dim]new[/]"
        extra = (f" · {task['timeouts']} timeout" if task["timeouts"] else "") + (f" · {task['errors']} error" if task["errors"] else "")
        tasks.add_row(name + mark, f"{task['passed']}/{task['runs']}{extra}", f"{task['median_seconds']:.0f}s",
                      f"{b['passed']}/{b['runs']}" if b else "-")
    console.print(tasks)

    weighting = "usage-weighted" if t.get("score_weighting") == "usage" else "equal weights"
    console.print(f"score {t['score']:.0%} ({weighting}) · {t['passed']}/{t['runs']} runs passed · "
                  f"median {t['median_seconds']:.0f}s · p90 {t['p90_seconds']:.0f}s · "
                  f"{t['timeouts']} timeouts · {t['errors']} errors · {t['nudges']} nudges · {t['tool_errors']} tool errors")
    if base is None:
        console.print("[dim]no baseline yet for this profile: `firox eval baseline` stores one[/]")
        return
    if cmp.digest_changed:
        console.print("[yellow]⚠ the model file changed since the baseline; score changes may come from the model[/]")
    for note in cmp.notes:
        style = "red" if "baseline, now" in note or note.startswith("category") else "dim"
        console.print(f"  [{style}]{escape(note)}[/]")
    if cmp.removed:
        console.print(f"  [dim]not in this run: {', '.join(cmp.removed)}[/]")
    console.print("[red]REGRESSIONS FOUND[/]" if cmp.has_regressions else "[green]no regressions against the baseline[/]")


def _history(console: Console, results_dir: Path, profile: str | None) -> None:
    files = sorted(results_dir.glob("*.json")) if results_dir.is_dir() else []
    profiles = sorted({load_summary(p)["profile"] for p in files}) if not profile else [profile]
    if not profiles:
        console.print(f"[yellow]no summaries in {results_dir}[/]")
        return
    for name in profiles:
        table = Table(title=name, box=None, pad_edge=False, show_edge=False, title_justify="left")
        for column in ("run", "firox", "model", "score", "passed", "median"):
            table.add_column(column)
        for s in history(name, results_dir):
            t = s["totals"]
            table.add_row(s["run_id"].split("_")[0], (s["firox_commit"] or "?")[:7], (s["model_digests"] or ["?"])[0][:12],
                          f"{t['score']:.0%}", f"{t['passed']}/{t['runs']}", f"{t['median_seconds']:.0f}s")
        console.print(table)
