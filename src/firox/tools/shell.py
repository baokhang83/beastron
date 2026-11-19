from __future__ import annotations

import subprocess

from ..events import Preview
from .base import Args, Tool, ToolResult
from .sandbox import Workspace

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
HEAD_CHARS = 4_000
TAIL_CHARS = 8_000  # errors and test summaries are usually at the end


def _clip(text: str) -> str:
    if len(text) <= HEAD_CHARS + TAIL_CHARS:
        return text
    skipped = len(text) - HEAD_CHARS - TAIL_CHARS
    return f"{text[:HEAD_CHARS]}\n\n[... {skipped} characters omitted ...]\n\n{text[-TAIL_CHARS:]}"


def _last_line(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        line = line.strip(" =-")
        if line:
            return line[:70]
    return ""


def make_shell_tool(ws: Workspace, prefix: list[str] | None = None) -> Tool:
    """`prefix`, if given, wraps every command, e.g. in a sandbox: prefix + ["/bin/sh", "-c", command]."""
    def preview(args: Args) -> Preview:
        return Preview("command", f"run_shell in {ws.root}", args["command"])

    def run_shell(args: Args) -> ToolResult:
        timeout = min(max(1, args.get("timeout") or DEFAULT_TIMEOUT), MAX_TIMEOUT)
        try:
            command = [*prefix, "/bin/sh", "-c", args["command"]] if prefix else args["command"]
            proc = subprocess.run(
                command,
                shell=not prefix,
                cwd=ws.root,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,  # a command waiting for input would hang forever
            )
        except subprocess.TimeoutExpired as e:
            partial = e.stdout or ""
            if isinstance(partial, bytes):  # Python can hand back bytes here even with text=True
                partial = partial.decode(errors="replace")
            return ToolResult(
                f"Timed out after {timeout}s.\n{_clip(partial)}",
                f"timed out after {timeout}s",
                ok=False,
            )
        parts = [f"exit code: {proc.returncode}"]
        if proc.stdout.strip():
            parts.append(f"--- stdout ---\n{proc.stdout.rstrip()}")
        if proc.stderr.strip():
            parts.append(f"--- stderr ---\n{proc.stderr.rstrip()}")
        summary = f"exit {proc.returncode}"
        last = _last_line(proc.stdout or proc.stderr)
        if last:
            summary += f" · {last}"
        return ToolResult(_clip("\n".join(parts)), summary, ok=proc.returncode == 0)

    return Tool(
        "run_shell",
        "Run a shell command in the project root (for example tests, linters, git). "
        "The user approves each command. Commands cannot read from stdin.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."},
                "timeout": {"type": "integer", "description": f"Seconds before it is stopped (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})."},
            },
            "required": ["command"],
        },
        run_shell,
        preview=preview,
    )
