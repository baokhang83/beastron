"""`firox usecases …` commands: find out what the owner really asks coding agents to do."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from . import claude_logs, codex_logs
from .requests import save, stats

usecases_app = typer.Typer(
    name="usecases",
    help="Read your coding-agent history to see which kinds of tasks you really do (stays on this machine).",
    add_completion=False,
    no_args_is_help=True,
)


@usecases_app.command("scan")
def scan(
    claude: Annotated[bool, typer.Option(help="Read Claude Code's session logs.")] = True,
    codex: Annotated[bool, typer.Option(help="Read Codex's session logs.")] = True,
) -> None:
    """Read the session logs into ~/.local/state/firox/usecases/ (counts only are printed)."""
    console = Console(highlight=False)
    sources = [("Claude Code", "claude", claude_logs.read_all, claude), ("Codex", "codex", codex_logs.read_all, codex)]
    for label, name, read, enabled in sources:
        if not enabled:
            continue
        requests = read()
        path = save(requests, name)
        s = stats(requests)
        console.print(f"{label}: {s['requests']} requests in {s['sessions']} sessions across {s['projects']} "
                      f"projects ({s['commands']} slash commands, {s['corrected']} immediately corrected)")
        console.print(f"[dim]  most used tools: {', '.join(f'{k} {v}' for k, v in s['top_tools'].items()) or 'none'}[/]")
        console.print(f"[dim]  saved locally (owner-only): {path}[/]")
