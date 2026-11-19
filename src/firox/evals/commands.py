"""`firox eval …` commands.

- `run`: run tasks, write a summary per model profile, compare with the baseline
- `report`: summarize a run (default: the latest) or show the history
- `baseline`: store a run as the reference for its profile(s)
- `propose`: list commits of a public repository that could become replay tasks
- `add`: draft a replay task from a commit, verify it, and write it after approval
- `verify`: check tasks fail as given and pass with their reference solution

`run` and `report` exit with status 1 when they find a regression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

import shutil
import tempfile
import time
from dataclasses import asdict

from rich.syntax import Syntax
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
from ..usecases import commits
from .task import TASKS_DIR, TaskError, load_task, load_tasks
from .verify import verify

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


# ----- mining commit-replay tasks ------------------------------------------------------

@eval_app.command("propose")
def propose(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="A local clone of one of your public repositories.")],
    category: Annotated[str | None, typer.Option("--category", help="Only commits that look like this category.")] = None,
    limit: Annotated[int, typer.Option(help="Show at most this many candidates.")] = 20,
    scan: Annotated[int, typer.Option(help="How many recent commits to look at.")] = 400,
    max_files: Annotated[int, typer.Option(help="Most files a candidate may change.")] = 3,
    max_lines: Annotated[int, typer.Option(help="Most changed lines a candidate may have.")] = 150,
) -> None:
    """List small commits that change code and tests, as candidate replay tasks (writes nothing)."""
    console = Console(highlight=False)
    try:
        info = commits.repo_info(repo)
        found = commits.candidates(info.path, scan=scan, max_files=max_files, max_lines=max_lines, category=category)
    except commits.MinerError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from None
    console.print(f"{info.url} · {info.visibility} · {info.license} · {len(found)} candidates in the last {scan} commits")
    table = Table(box=None, pad_edge=False, show_edge=False)
    for column in ("commit", "category", "files", "lines", "tests", "subject"):
        table.add_column(column, justify="right" if column in ("files", "lines", "tests") else "left")
    for c in found[:limit]:
        table.add_row(c.sha[:7], c.category, str(len(c.files)), str(c.lines), str(len(c.tests)), escape(c.subject[:70]))
    console.print(table)
    if found:
        console.print(f"[dim]add one: firox eval add --repo {repo} --commit <commit>[/]")


@eval_app.command("add")
def add(
    repo: Annotated[Path, typer.Option("--repo", "-r", help="A local clone of one of your public repositories.")],
    commit: Annotated[str, typer.Option("--commit", help="The commit to replay.")],
    category: Annotated[str | None, typer.Option("--category", help="Override the guessed category.")] = None,
    prompt: Annotated[str | None, typer.Option("--prompt", help="The instruction for the agent (default: drafted from the commit message).")] = None,
    tasks_dir: Annotated[Path, typer.Option(help="Folder with the eval tasks.")] = TASKS_DIR,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Write without asking (after a successful verification).")] = False,
    verify_task: Annotated[bool, typer.Option("--verify/--no-verify", help="Check that it fails before and passes after the commit.")] = True,
) -> None:
    """Draft a replay task from a commit, verify it, show it, and write it only if you approve."""
    console = Console(highlight=False)
    try:
        info = commits.repo_info(repo)
        sha = commits._git(info.path, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
        [candidate] = [c for c in commits.candidates(info.path, scan=100_000, max_files=10**6, max_lines=10**9)
                       if c.sha == sha] or [None]
        if candidate is None:
            raise commits.MinerError(f"{commit} doesn't change both source code and tests, so it can't be replayed")
        task_name = commits.task_id(info, candidate)
        text = commits.draft_toml(info, candidate, category=category, prompt=prompt)
    except commits.MinerError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from None
    destination = tasks_dir / task_name
    if destination.exists():
        console.print(f"[red]{destination} already exists[/]")
        raise typer.Exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        draft_dir = Path(tmp) / task_name
        draft_dir.mkdir()
        (draft_dir / "task.toml").write_text(text, encoding="utf-8")
        try:
            task = load_task(draft_dir)
        except TaskError as e:
            console.print(f"[red]the draft is invalid: {escape(str(e))}[/]")
            raise typer.Exit(1) from None
        console.print(Syntax(text, "toml", theme="ansi_dark", background_color="default"))
        if verify_task:
            console.print("[dim]verifying: the checks must fail on the parent commit and pass on the real commit ...[/]")
            result = verify(task)
            console.print(("[green]✓[/]" if result.fails_as_given else "[red]✗[/]") + " fails on the parent commit")
            console.print(("[green]✓[/]" if result.passes_with_solution else "[red]✗[/]") + " passes on the real commit")
            for check in result.with_solution:
                if not check.passed:
                    console.print(f"  [red]{escape(check.reason.splitlines()[0])}[/]")
            if not result.ok:
                console.print("[red]Not written: the task doesn't separate failing from passing work.[/]")
                raise typer.Exit(1)
        if not yes and not typer.confirm(f"Write {destination}?", default=False):
            console.print("Not written.")
            raise typer.Exit(1)
        destination.mkdir(parents=True)
        shutil.copy2(draft_dir / "task.toml", destination / "task.toml")
    console.print(f"written: {destination / 'task.toml'}")


@eval_app.command("verify")
def verify_cmd(
    suite: Annotated[str | None, typer.Option("--suite", "-s", help="Only tasks with this tag.")] = None,
    task: Annotated[list[str] | None, typer.Option("--task", "-t", help="Only this task id (repeatable).")] = None,
    tasks_dir: Annotated[Path, typer.Option(help="Folder with the eval tasks.")] = TASKS_DIR,
) -> None:
    """Check that every task fails as given and passes with its reference solution (no model involved)."""
    console = Console(highlight=False)
    try:
        tasks = load_tasks(tasks_dir, tag=suite, ids=task or None)
    except TaskError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from None
    bad = 0
    for t in tasks:
        started = time.monotonic()
        try:
            result = verify(t)
            if result.ok:
                status = "[green]ok[/]"
            else:
                bad += 1
                status = "[red]passes without work[/]" if not result.fails_as_given else "[red]fails with its solution[/]"
                for check in result.with_solution:
                    if not check.passed:
                        status += f"\n    [dim]{escape(check.reason.splitlines()[0])}[/]"
        except Exception as e:  # a broken task must not stop the others
            bad += 1
            status = f"[red]error: {escape(str(e).splitlines()[0][:150])}[/]"
        console.print(f"{t.id:<60} {time.monotonic() - started:5.1f}s  {status}")
    console.print(f"\n{len(tasks) - bad}/{len(tasks)} tasks verified")
    raise typer.Exit(1 if bad else 0)
