from __future__ import annotations

import difflib
import os
import re
from collections.abc import Iterator
from pathlib import Path

from ..events import Preview
from .base import Args, Tool, ToolError, ToolResult
from .sandbox import Workspace

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", "dist", "build",
    ".next", ".nuxt", "target", ".idea", ".vscode", ".DS_Store", ".eggs",
}
MAX_LIST = 400
MAX_MATCHES = 100
READ_LINES = 500
READ_CHARS = 40_000
MAX_SEARCH_FILE = 1_000_000


def _walk(ws: Workspace, base: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            if name not in IGNORED_DIRS:
                yield Path(dirpath) / name


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return b"\0" in f.read(4096)
    except OSError:
        return True


def _read_text(ws: Workspace, path: Path) -> str:
    if not path.exists():
        raise ToolError(f"File not found: {ws.rel(path)}")
    if path.is_dir():
        raise ToolError(f"{ws.rel(path)} is a directory. Use list_files to see its contents.")
    if _is_binary(path):
        raise ToolError(f"{ws.rel(path)} looks like a binary file.")
    return path.read_text(encoding="utf-8", errors="replace")


def _diff(rel: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3,
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _count_changes(diff: str) -> tuple[int, int]:
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    return added, removed


def make_file_tools(ws: Workspace) -> list[Tool]:
    def list_files(args: Args) -> ToolResult:
        base = ws.resolve(args.get("path") or ".")
        if not base.is_dir():
            raise ToolError(f"Not a directory: {args.get('path')}")
        files = []
        for path in _walk(ws, base):
            files.append(ws.rel(path))
            if len(files) >= MAX_LIST:
                break
        out = "\n".join(files) or "(no files)"
        if len(files) >= MAX_LIST:
            out += f"\n... stopped at {MAX_LIST} files. List a subdirectory to see more."
        return ToolResult(out, f"{len(files)} files")

    def search_code(args: Args) -> ToolResult:
        pattern = args["pattern"]
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))  # treat it as plain text
        base = ws.resolve(args.get("path") or ".")
        paths = [base] if base.is_file() else _walk(ws, base)
        matches: list[str] = []
        hit_files: set[str] = set()
        for path in paths:
            if path.stat().st_size > MAX_SEARCH_FILE or _is_binary(path):
                continue
            rel = ws.rel(path)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{n}: {line.strip()[:200]}")
                    hit_files.add(rel)
                    if len(matches) >= MAX_MATCHES:
                        break
            if len(matches) >= MAX_MATCHES:
                break
        out = "\n".join(matches) or "No matches."
        if len(matches) >= MAX_MATCHES:
            out += f"\n... stopped at {MAX_MATCHES} matches. Use a more specific pattern or path."
        return ToolResult(out, f"{len(matches)} matches in {len(hit_files)} files")

    def read_file(args: Args) -> ToolResult:
        path = ws.resolve(args["path"])
        lines = _read_text(ws, path).splitlines(keepends=True)
        if not lines:
            return ToolResult("(empty file)", "empty")
        total = len(lines)
        offset = max(1, args.get("offset") or 1)
        if offset > total:
            raise ToolError(f"offset {offset} is past the end of the file ({total} lines).")
        limit = max(1, args.get("limit") or READ_LINES)
        shown: list[str] = []
        size = 0
        for line in lines[offset - 1 : offset - 1 + limit]:
            if shown and size + len(line) > READ_CHARS:
                break
            shown.append(line)
            size += len(line)
        end = offset - 1 + len(shown)
        out = "".join(shown)
        if offset == 1 and end == total:
            return ToolResult(out, f"{total} lines")
        out += f"\n[showing lines {offset}-{end} of {total}"
        if end < total:
            out += f"; call read_file with offset={end + 1} to continue"
        out += "]"
        return ToolResult(out, f"lines {offset}-{end} of {total}")

    def write_preview(args: Args) -> Preview:
        path = ws.resolve(args["path"], for_write=True)
        if path.is_dir():
            raise ToolError(f"{args['path']} is a directory.")
        old = _read_text(ws, path) if path.exists() else ""
        title = f"write_file {ws.rel(path)}" + ("" if path.exists() else " (new file)")
        return Preview("diff", title, _diff(ws.rel(path), old, args["content"]))

    def write_file(args: Args) -> ToolResult:
        path = ws.resolve(args["path"], for_write=True)
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        n = len(args["content"].splitlines())
        verb = "Overwrote" if existed else "Created"
        return ToolResult(f"{verb} {ws.rel(path)} ({n} lines).", f"{verb.lower()} · {n} lines")

    def _apply_edit(args: Args) -> tuple[Path, str, str]:
        path = ws.resolve(args["path"], for_write=True)
        old_text, new_text = args["old_text"], args["new_text"]
        if not old_text:
            raise ToolError("old_text is empty. To create or replace a whole file, use write_file.")
        content = _read_text(ws, path)
        count = content.count(old_text)
        if count == 0:
            raise ToolError(
                f"old_text was not found in {ws.rel(path)}. It must match the file exactly, "
                "including indentation and whitespace. Read the file again and copy the text exactly."
            )
        if count > 1:
            raise ToolError(
                f"old_text appears {count} times in {ws.rel(path)}. "
                "Include more surrounding lines so it matches exactly one place."
            )
        return path, content, content.replace(old_text, new_text, 1)

    def edit_preview(args: Args) -> Preview:
        path, old, new = _apply_edit(args)
        return Preview("diff", f"edit_file {ws.rel(path)}", _diff(ws.rel(path), old, new))

    def edit_file(args: Args) -> ToolResult:
        path, old, new = _apply_edit(args)
        path.write_text(new, encoding="utf-8")
        added, removed = _count_changes(_diff(ws.rel(path), old, new))
        return ToolResult(f"Edited {ws.rel(path)}.", f"+{added} -{removed}")

    path_prop = {"type": "string", "description": "Path relative to the project root."}
    return [
        Tool(
            "list_files",
            "List files in the project (recursively), skipping build and dependency folders.",
            {"type": "object", "properties": {"path": {**path_prop, "description": "Directory to list. Defaults to the project root."}}},
            list_files,
        ),
        Tool(
            "search_code",
            "Search file contents with a regular expression. Returns matching lines as path:line: text.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression (Python syntax) or plain text."},
                    "path": {**path_prop, "description": "File or directory to search. Defaults to the project root."},
                },
                "required": ["pattern"],
            },
            search_code,
        ),
        Tool(
            "read_file",
            f"Read a text file. Returns up to {READ_LINES} lines; use offset and limit for longer files.",
            {
                "type": "object",
                "properties": {
                    "path": path_prop,
                    "offset": {"type": "integer", "description": "First line to read (1-based)."},
                    "limit": {"type": "integer", "description": "Maximum number of lines to read."},
                },
                "required": ["path"],
            },
            read_file,
        ),
        Tool(
            "write_file",
            "Create a new file, or replace a file's entire content. For changes to an existing file, prefer edit_file.",
            {
                "type": "object",
                "properties": {"path": path_prop, "content": {"type": "string", "description": "The complete file content."}},
                "required": ["path", "content"],
            },
            write_file,
            preview=write_preview,
        ),
        Tool(
            "edit_file",
            "Replace one exact piece of text in a file. old_text must match exactly one place in the file, "
            "including whitespace. Read the file first.",
            {
                "type": "object",
                "properties": {
                    "path": path_prop,
                    "old_text": {"type": "string", "description": "Exact text to replace, copied from the file."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
            edit_file,
            preview=edit_preview,
        ),
    ]
