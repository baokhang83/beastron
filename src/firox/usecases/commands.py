"""`firox usecases …` commands: find out what the owner really asks coding agents to do."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from ..config import ConfigError, load_config
from . import claude_logs, codex_logs
from .classify import classify_all, model_asker, summary, write_markdown
from .requests import load, save, stats, usecases_dir

USECASES_FILE = Path("evals/usecases.md")

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
    since: Annotated[str | None, typer.Option(help="Only requests from this period: 90d, 12w, or a date (2026-06-01).")] = None,
) -> None:
    """Read the session logs into ~/.local/state/firox/usecases/ (counts only are printed)."""
    console = Console(highlight=False)
    try:
        cutoff = parse_since(since) if since else None
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/]")
        raise typer.Exit(1) from None
    sources = [("Claude Code", "claude", claude_logs.read_all, claude), ("Codex", "codex", codex_logs.read_all, codex)]
    for label, name, read, enabled in sources:
        if not enabled:
            continue
        requests = read()
        if cutoff:
            requests = [r for r in requests if r.timestamp and _when(r.timestamp) >= cutoff]
        path = save(requests, name)
        s = stats(requests)
        console.print(f"{label}: {s['requests']} requests in {s['sessions']} sessions across {s['projects']} "
                      f"projects ({s['commands']} slash commands, {s['corrected']} immediately corrected)")
        console.print(f"[dim]  most used tools: {', '.join(f'{k} {v}' for k, v in s['top_tools'].items()) or 'none'}[/]")
        console.print(f"[dim]  saved locally (owner-only): {path}[/]")


def parse_since(value: str) -> datetime:
    """"90d", "12w" or "2026-06-01" -> the earliest time to keep (UTC)."""
    now = datetime.now(timezone.utc)
    match = re.fullmatch(r"(\d+)([dw])", value.strip())
    if match:
        days = int(match.group(1)) * (7 if match.group(2) == "w" else 1)
        return now - timedelta(days=days)
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"--since {value!r}: use a number of days or weeks (90d, 12w) or a date (2026-06-01)") from None


def _when(timestamp: str) -> datetime:
    when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _load_scanned(console: Console) -> list:
    requests = []
    for name in ("claude", "codex"):
        path = usecases_dir() / f"{name}.jsonl"
        if path.is_file():
            requests += load(path)
    if not requests:
        console.print("[red]Nothing scanned yet: run `firox usecases scan` first.[/]")
        raise typer.Exit(1)
    return requests


@usecases_app.command("classify")
def classify_cmd(
    write: Annotated[bool, typer.Option(help=f"Write the counts and weights to {USECASES_FILE} (and .json).")] = False,
    out: Annotated[Path, typer.Option(help="Where --write puts the summary.")] = USECASES_FILE,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Ask this local model profile about requests the rules can't place.")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Path to models.toml.")] = None,
) -> None:
    """Sort the scanned requests into categories; with --write, publish counts and weights (no request text)."""
    console = Console(highlight=False)
    requests = _load_scanned(console)
    ask = None
    if model:
        try:
            from ..client import OpenAICompatClient
            ask = model_asker(OpenAICompatClient(load_config(config).get(model)))
        except ConfigError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            raise typer.Exit(1) from None
    results = classify_all(requests, ask)
    local = usecases_dir() / "classified.jsonl"
    with local.open("w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps({"category": item.category, "reason": item.reason, "source": item.request.source,
                                "session": item.request.session, "position": item.request.position,
                                "text": item.request.text}, ensure_ascii=False) + "\n")
    local.chmod(0o600)
    data = summary(results)
    console.print(f"{data['requests']} requests, {data['task_requests']} of them tasks")
    for category, weight in data["weights"].items():
        console.print(f"  {category:<9} {data['counts'][category]:>5}  {weight:>6.1%}")
    for kind in ("followup", "meta", "unclear"):
        if data["counts"].get(kind):
            console.print(f"  [dim]{kind:<9} {data['counts'][kind]:>5}  (no weight)[/]")
    console.print(f"[dim]each request with its category, locally (owner-only): {local}[/]")
    if write:
        write_markdown(data, out)
        console.print(f"published counts and weights: {out} and {out.with_suffix('.json')}")


@usecases_app.command("sample")
def sample_cmd(
    count: Annotated[int, typer.Option("--count", "-n", min=1, help="How many requests to show.")] = 20,
    seed: Annotated[int | None, typer.Option(help="Random seed, to get the same sample again.")] = None,
) -> None:
    """Show random requests with their category, to check the classifier by hand (local only)."""
    console = Console(highlight=False)
    results = classify_all(_load_scanned(console))
    rng = random.Random(seed)
    for item in rng.sample(results, min(count, len(results))):
        console.print(f"[bold]{item.category or 'unclear':<9}[/] [dim]{item.reason}[/]\n  {escape(item.request.text[:200])}")
