"""`firox eval …` commands.

The commands are declared here first so the CLI shape is fixed; each one is
filled in by its milestone issue (M0 in docs/plan.md § 6). Until then a command
says which issue will bring it and exits with status 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from ..config import ConfigError, load_config
from ..ui import RichUI
from . import runner, sandbox
from .runner import RunResult
from .task import TASKS_DIR, TaskError, load_tasks

eval_app = typer.Typer(
    name="eval",
    help="Measure firox on the eval suite (see docs/plan.md).",
    add_completion=False,
    no_args_is_help=True,
)

NOT_YET = 2  # exit status for commands that aren't implemented yet


def _not_yet(issue: int) -> None:
    typer.echo(f"Not implemented yet: see https://github.com/baokhang83/firox/issues/{issue}", err=True)
    raise typer.Exit(NOT_YET)


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
) -> None:
    """Run eval tasks for one or more model profiles."""
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
    console.print(f"\n{passed}/{len(results)} runs passed · results: {run_dir / 'results.jsonl'}")


@eval_app.command("report")
def report() -> None:
    """Show results per category and flag regressions against the baseline."""
    _not_yet(11)


@eval_app.command("baseline")
def baseline() -> None:
    """Store the latest results as the baseline for a model profile."""
    _not_yet(11)
