"""Command-line entry point and the interactive prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from typer.core import TyperGroup
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory

from .agent import Agent
from .policy import WORK
from .client import OpenAICompatClient
from .config import Config, ConfigError, load_config
from .evals.commands import eval_app
from .usecases.commands import usecases_app
from .tools import Workspace, make_tools
from .trace import SessionTrace, fan_out, model_digest
from .ui import RichUI, fmt_tokens

COMMANDS = {
    "/help": "show this help",
    "/models": "list the model profiles in models.toml",
    "/model": "switch model: /model NAME",
    "/do": "run a request with all tools, even if it reads like a question: /do REQUEST",
    "/clear": "start a fresh conversation",
    "/context": "show how full the context is",
    "/verbose": "toggle showing reasoning and full tool output",
    "/quit": "exit (or Ctrl-D)",
}

HISTORY_FILE = Path("~/.config/beastron/history").expanduser()

DEFAULT_COMMAND = "chat"


class DefaultToChat(TyperGroup):
    """A command group where anything that isn't a subcommand goes to `chat`.

    So `beastron`, `beastron "fix the test"` and `beastron -m fast "..."` keep working
    as before, while `beastron eval ...` reaches the eval commands. A task that is
    literally a subcommand's name can be passed as `beastron chat eval`.
    """

    def parse_args(self, ctx, args):
        if not args or (args[0] not in self.commands and args[0] not in ("--help", "-h")):
            args = [DEFAULT_COMMAND, *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=DefaultToChat,
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["--help", "-h"]},
    help="beastron: a coding agent for local and open-weight models.\n\n"
    "Run `beastron` for an interactive session, or `beastron \"a task\"` to run one task.",
)
app.add_typer(eval_app)
app.add_typer(usecases_app)


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for cmd, help_text in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=help_text)


@app.command(DEFAULT_COMMAND)
def main(
    task: Annotated[str | None, typer.Argument(help="Run this one task and exit. Omit for interactive mode.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model profile from models.toml.")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Path to models.toml.")] = None,
    max_steps: Annotated[int, typer.Option(help="Maximum model calls per task.")] = 25,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show reasoning and full tool output.")] = False,
    briefing: Annotated[
        bool, typer.Option("--briefing/--no-briefing", help="Read the project's docs (AGENTS.md, README.md, ...) at startup.")
    ] = True,
    trace: Annotated[
        bool, typer.Option("--trace/--no-trace", help="Record the session in ~/.local/state/beastron/sessions/.")
    ] = True,
) -> None:
    """Interactive session, or one task (the default: `beastron` and `beastron "task"` run this)."""
    ui = RichUI(verbose=verbose)
    try:
        cfg = load_config(config)
        profile = cfg.get(model)
        client = OpenAICompatClient(profile)
    except ConfigError as e:
        ui.error(str(e))
        raise typer.Exit(1) from None

    ws = Workspace(Path.cwd())
    session = SessionTrace.start(ws, profile) if trace else None
    agent = Agent(
        client, ws, make_tools(ws), on_event=fan_out(ui.handle, session), approve=ui.approve,
        max_steps=max_steps, briefing=briefing,
    )
    if session:
        session.note("briefing", files=[f.path for f in agent.briefing.files], chars=agent.briefing.chars)
    ui.header(profile, ws.root, agent.briefing if briefing else None)

    try:
        if task:
            reason = agent.run(task)
            raise typer.Exit(0 if reason == "done" else 1)
        _repl(agent, ui, cfg, trace=session)
    finally:
        if session:
            session.close()
            ui.info(f"session trace: {session.path}")


def _repl(agent: Agent, ui: RichUI, cfg: Config, trace: SessionTrace | None = None) -> None:
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
            if _command(line, agent, ui, cfg, trace) == "quit":
                break
            continue
        ui.console.print()
        agent.run(line)


def _command(line: str, agent: Agent, ui: RichUI, cfg: Config, trace: SessionTrace | None = None) -> str | None:
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
        if trace:
            trace.note("model_switch", profile=agent.client.profile.name, model=agent.client.profile.model,
                         digest=model_digest(agent.client.profile))
        ui.info("switched model; the conversation so far is kept (/clear to start fresh)")
        ui.header(agent.client.profile, agent.workspace.root)
    elif cmd == "/do":
        if not arg:
            ui.info("use /do REQUEST: it runs the request with every tool, even when it reads like a question")
            return None
        ui.console.print()
        agent.run(arg, intent=WORK)
    elif cmd == "/clear":
        agent.reset()
        if trace:
            trace.note("clear")
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
