"""Command-line entry point and the interactive prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory

from .agent import Agent
from .client import OpenAICompatClient
from .config import Config, ConfigError, load_config
from .tools import Workspace, make_tools
from .ui import RichUI, fmt_tokens

COMMANDS = {
    "/help": "show this help",
    "/models": "list the model profiles in models.toml",
    "/model": "switch model: /model NAME",
    "/clear": "start a fresh conversation",
    "/context": "show how full the context is",
    "/verbose": "toggle showing reasoning and full tool output",
    "/quit": "exit (or Ctrl-D)",
}

HISTORY_FILE = Path("~/.config/firox/history").expanduser()

app = typer.Typer(add_completion=False, rich_markup_mode="rich")


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for cmd, help_text in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=help_text)


@app.command()
def main(
    task: Annotated[str | None, typer.Argument(help="Run this one task and exit. Omit for interactive mode.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model profile from models.toml.")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Path to models.toml.")] = None,
    max_steps: Annotated[int, typer.Option(help="Maximum model calls per task.")] = 25,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show reasoning and full tool output.")] = False,
    briefing: Annotated[
        bool, typer.Option("--briefing/--no-briefing", help="Read the project's docs (AGENTS.md, README.md, ...) at startup.")
    ] = True,
) -> None:
    """firox: a coding agent for local and open-weight models."""
    ui = RichUI(verbose=verbose)
    try:
        cfg = load_config(config)
        profile = cfg.get(model)
        client = OpenAICompatClient(profile)
    except ConfigError as e:
        ui.error(str(e))
        raise typer.Exit(1) from None

    ws = Workspace(Path.cwd())
    agent = Agent(
        client, ws, make_tools(ws), on_event=ui.handle, approve=ui.approve,
        max_steps=max_steps, briefing=briefing,
    )
    ui.header(profile, ws.root, agent.briefing if briefing else None)

    if task:
        reason = agent.run(task)
        raise typer.Exit(0 if reason == "done" else 1)
    _repl(agent, ui, cfg)


def _repl(agent: Agent, ui: RichUI, cfg: Config) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        completer=SlashCompleter(),
        complete_while_typing=True,
    )
    while True:
        try:
            line = session.prompt(HTML("<ansicyan><b>›</b></ansicyan> ")).strip()
        except KeyboardInterrupt:
            continue  # Ctrl-C at the prompt clears the line
        except EOFError:
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", ":q"):  # people type these without the slash
            break
        if line.startswith("/"):
            if _command(line, agent, ui, cfg) == "quit":
                break
            continue
        ui.console.print()
        agent.run(line)


def _command(line: str, agent: Agent, ui: RichUI, cfg: Config) -> str | None:
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd in ("/quit", "/exit"):
        return "quit"
    if cmd == "/help":
        for name, help_text in COMMANDS.items():
            ui.console.print(f"  [bold]{name:<10}[/] [dim]{help_text}[/]")
    elif cmd == "/models":
        for name, p in cfg.profiles.items():
            mark = "●" if name == agent.client.profile.name else " "
            ui.console.print(f"  [cyan]{mark}[/] [bold]{name}[/] [dim]{p.model} @ {p.base_url}[/]")
    elif cmd == "/model":
        if not arg:
            ui.info(f"current model: {agent.client.profile.name}. Use /model NAME (see /models).")
            return None
        try:
            agent.client = OpenAICompatClient(cfg.get(arg))
        except ConfigError as e:
            ui.error(str(e))
            return None
        ui.info("switched model; the conversation so far is kept (/clear to start fresh)")
        ui.header(agent.client.profile, agent.workspace.root)
    elif cmd == "/clear":
        agent.reset()
        ui.context = (0, agent.client.profile.context)
        ui.info("started a fresh conversation")
        if agent.use_briefing:
            ui.briefing_line(agent.briefing)
    elif cmd == "/context":
        used, limit = agent.context_used, agent.client.profile.context
        pct = 100 * used / limit if limit else 0
        ui.info(f"context: {fmt_tokens(used)} / {fmt_tokens(limit)} tokens ({pct:.0f}%), {len(agent.messages)} messages")
    elif cmd == "/verbose":
        ui.verbose = not ui.verbose
        ui.info(f"verbose {'on' if ui.verbose else 'off'}")
    else:
        ui.info(f"unknown command {cmd}. Try /help.")
    return None


def run() -> None:
    app()
