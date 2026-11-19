"""The terminal UI. It listens to agent events and draws them with Rich."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from .config import ModelProfile
from .events import (
    ApprovalRequest,
    ContextUpdate,
    ErrorEvent,
    Event,
    ModelEnd,
    ModelStart,
    Nudge,
    ReasoningDelta,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnEnd,
)

ACCENT = "cyan"
MAX_PREVIEW_LINES = 80
MAX_VERBOSE_LINES = 30


def fmt_tokens(n: int) -> str:
    if n >= 1024 and n % 1024 == 0:  # context sizes: 32768 -> 32K
        return f"{n // 1024}K"
    return f"{n / 1000:.1f}K" if n >= 1000 else str(n)


def fmt_path(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


class _Tail:
    """Renders only the last screenful of a renderable, for live streaming."""

    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = console.render_lines(self.renderable, options, pad=False)
        for line in lines[-max(console.size.height - 3, 3) :]:
            yield from line
            yield Segment.line()


class _Thinking:
    """Spinner with elapsed time, recomputed on every refresh."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.reasoning_chars = 0
        self.spinner = Spinner("dots", style=ACCENT)

    def __rich__(self) -> RenderableType:
        elapsed = time.monotonic() - self.started
        label = "Thinking" if self.reasoning_chars else "Waiting for the model"
        extra = f" · {fmt_tokens(self.reasoning_chars // 4)} tokens" if self.reasoning_chars else ""
        self.spinner.update(text=Text(f" {label} · {elapsed:.0f}s{extra}", style="dim"))
        return self.spinner


class RichUI:
    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console(highlight=False)
        self.verbose = verbose
        self.always_allow: set[str] = set()
        self.context = (0, 0)
        self.root: Path | None = None
        self._live: Live | None = None
        self._thinking: _Thinking | None = None
        self._text = ""
        self._reasoning = ""

    # ----- header and messages -------------------------------------------

    def header(self, profile: ModelProfile, root: Path) -> None:
        self.root = root
        line = Text()
        line.append(" firox ", style=f"bold black on {ACCENT}")
        line.append(f"  {profile.name}", style="bold")
        line.append(f" ({profile.model})", style="dim")
        line.append(f"  ·  {fmt_path(root)}  ·  ctx {fmt_tokens(profile.context)}", style="dim")
        self.console.print(line)
        self.console.print(Text("  /help for commands · Ctrl-C stops the agent · Ctrl-D quits", style="dim"))
        self.console.print()

    def info(self, message: str) -> None:
        self.console.print(Text(f"  {message}", style="dim"))

    def error(self, message: str) -> None:
        self.console.print(Panel(Text(message), title="error", title_align="left", border_style="red"))

    # ----- agent events ----------------------------------------------------

    def handle(self, event: Event) -> None:
        match event:
            case ModelStart():
                self._text = self._reasoning = ""
                self._thinking = _Thinking()
                self._start_live(self._thinking)
            case ReasoningDelta(text=t):
                self._reasoning += t
                if self._thinking:
                    self._thinking.reasoning_chars += len(t)
            case TextDelta(text=t):
                self._text += t
                if self._live:
                    self._live.update(_Tail(Markdown(self._text)))
            case ModelEnd():
                self._stop_live()
                if self.verbose and self._reasoning.strip():
                    self.console.print(Text(self._reasoning.strip(), style="dim italic"))
                    self.console.print()
                if self._text.strip():
                    self.console.print(Markdown(self._text.strip()))
                    self.console.print()
            case ToolStart(name=name, args=args):
                line = Text()
                line.append("⏺ ", style=ACCENT)
                line.append(name, style="bold")
                summary = _arg_summary(name, args, self.root)
                if summary:
                    line.append(f"  {summary}", style="dim")
                self.console.print(line)
            case ToolEnd(ok=ok, summary=summary, output=output):
                mark, style = ("✓", "green") if ok else ("✗", "red")
                line = Text("  ⎿ ", style="dim")
                line.append(f"{mark} ", style=style)
                line.append(summary, style="dim" if ok else style)
                self.console.print(line)
                if self.verbose and output.strip():
                    self.console.print(Text(_head(output, MAX_VERBOSE_LINES), style="dim"), soft_wrap=False)
            case Nudge(reason=reason):
                self.console.print(Text(f"↻ {reason}; asking it to continue", style="yellow dim"))
            case ContextUpdate(used=used, limit=limit):
                self.context = (used, limit)
            case ErrorEvent(message=message):
                self._stop_live()
                self.error(message)
            case TurnEnd() as end:
                self._stop_live()
                self._turn_summary(end)

    def _turn_summary(self, end: TurnEnd) -> None:
        marks = {
            "done": ("✓ done", "green"),
            "denied": ("✗ stopped: you denied the action; tell firox what to do instead", "yellow"),
            "max_steps": ("⚠ stopped at the step limit", "yellow"),
            "interrupted": ("✗ interrupted", "yellow"),
            "error": ("✗ failed", "red"),
        }
        label, style = marks[end.reason]
        parts = [f"{end.steps} step{'s' if end.steps != 1 else ''}"]
        tokens = end.prompt_tokens + end.completion_tokens
        if tokens:
            parts.append(f"{fmt_tokens(tokens)} tokens")
        parts.append(f"{end.elapsed:.0f}s")
        if end.completion_tokens and end.model_seconds > 0:
            parts.append(f"{end.completion_tokens / end.model_seconds:.0f} tok/s")
        used, limit = self.context
        if limit:
            parts.append(f"ctx {fmt_tokens(used)}/{fmt_tokens(limit)}")
        line = Text(" ")
        line.append(label, style=style)
        line.append(" · " + " · ".join(parts), style="dim")
        self.console.print(line)
        self._change_record(end)
        if limit and used > 0.8 * limit:
            self.console.print(Text("  context is nearly full; /clear starts a fresh conversation", style="yellow"))
        self.console.print()

    def _change_record(self, end: TurnEnd) -> None:
        """What really changed, from the tools. The model's summary above can be wrong."""
        letters = {"created": ("A", "green"), "modified": ("M", "yellow"), "deleted": ("D", "red")}
        if end.changes:
            self.console.print(Text("   files changed by firox:", style="dim"))
            for change in end.changes:
                letter, style = letters[change.kind]
                line = Text("     ")
                line.append(letter, style=f"bold {style}")
                line.append(f" {change.path}")
                if change.kind != "deleted":
                    line.append(f"  +{change.added} -{change.removed}", style="dim")
                self.console.print(line)
        elif end.reason != "error":
            self.console.print(Text("   no files changed by firox's file tools", style="dim"))
        if end.shell_commands:
            n = end.shell_commands
            self.console.print(
                Text(f"   + {n} shell command{'s' if n != 1 else ''}, which may also have changed files", style="dim")
            )

    # ----- approvals -------------------------------------------------------

    def approve(self, request: ApprovalRequest) -> bool:
        self._stop_live()
        p = request.preview
        body = _head(p.body, MAX_PREVIEW_LINES)
        lexer = "diff" if p.kind == "diff" else "bash"
        self.console.print(
            Panel(
                Syntax(body or "(no changes)", lexer, theme="ansi_dark", word_wrap=True, background_color="default"),
                title=p.title,
                title_align="left",
                border_style="dim",
                padding=(0, 1),
            )
        )
        if request.tool in self.always_allow:
            self.info(f"allowed (always allow {request.tool} is on)")
            return True
        while True:
            try:
                answer = self.console.input(
                    f"  [bold]Allow?[/] [{ACCENT}]y[/]es / [{ACCENT}]n[/]o / [{ACCENT}]a[/]lways for {request.tool} this session › "
                ).strip().lower()
            except EOFError:
                return False
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "always"):
                self.always_allow.add(request.tool)
                return True

    # ----- live area -------------------------------------------------------

    def _start_live(self, renderable: RenderableType) -> None:
        self._stop_live()
        self._live = Live(
            renderable,
            console=self.console,
            refresh_per_second=10,
            transient=True,  # cleared when done; the final text is printed normally
            vertical_overflow="visible",
        )
        self._live.start()

    def _stop_live(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None


def _arg_summary(name: str, args: dict[str, Any], root: Path | None = None) -> str:
    if name == "run_shell":
        value = args.get("command", "")
    elif name == "git":
        value = " ".join(str(v) for v in (args.get("command", ""), args.get("ref"), args.get("path")) if v)
    elif name == "search_code":
        value = repr(args.get("pattern", "")) + (f" in {args['path']}" if args.get("path") else "")
    elif "path" in args:
        value = str(args["path"])
        if name == "read_file" and args.get("offset"):
            value += f" (from line {args['offset']})"
    elif name == "list_files":
        value = "."
    else:
        value = json.dumps(args, ensure_ascii=False)
    value = " ".join(str(value).split())  # one line
    if root:  # models often send absolute paths; show them relative to the project
        value = value.replace(f"{root}/", "").replace(str(root), ".")
    return value if len(value) <= 90 else value[:87] + "..."


def _head(text: str, max_lines: int) -> str:
    lines = text.rstrip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines]) + f"\n... {len(lines) - max_lines} more lines"
