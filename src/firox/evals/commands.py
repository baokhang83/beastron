"""`firox eval …` commands.

The commands are declared here first so the CLI shape is fixed; each one is
filled in by its milestone issue (M0 in docs/plan.md § 6). Until then a command
says which issue will bring it and exits with status 2.
"""

from __future__ import annotations

import typer

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
def run_suite() -> None:
    """Run eval tasks for one or more model profiles."""
    _not_yet(10)


@eval_app.command("report")
def report() -> None:
    """Show results per category and flag regressions against the baseline."""
    _not_yet(11)


@eval_app.command("baseline")
def baseline() -> None:
    """Store the latest results as the baseline for a model profile."""
    _not_yet(11)
