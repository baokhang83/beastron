"""Project briefing: documentation beastron reads before the model's first step.

Small models often answer questions about a project from file names alone,
without opening the README. So beastron reads the docs itself when a session
starts and puts them in the system prompt:

1. Pick a starting file: AGENTS.md, else README.md, else the most likely
   looking other Markdown file (shallowest folder first).
2. Read it fully (very large files are cut, with a note on how to continue).
3. Follow the Markdown files it links to, and the files those link to (two
   levels), most informative names first, skipping files like CHANGELOG or
   LICENSE that say nothing about what the project is.
4. Stop at a size budget. Linked files that didn't fit are listed by name so
   the model knows they exist.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .tools import Workspace
from .tools.files import IGNORED_DIRS, _is_binary, _walk

ENTRY_NAMES = ("agents.md", "readme.md")  # checked in this order, case-insensitive
MARKDOWN = {".md", ".markdown"}
MAX_DEPTH = 2  # starting file = 0, its links = 1, their links = 2
PER_FILE = 16_000  # characters
MIN_BUDGET, MAX_BUDGET = 8_000, 60_000
MIN_USEFUL = 1_500  # a linked file cut shorter than this isn't worth including
MAX_FALLBACK_SCAN = 5_000  # files to look at when searching for any .md
MAX_TOP_LEVEL = 60
MAX_NOT_READ = 20

# Files that don't help with "what is this project": history, legal, process.
_SKIP = re.compile(
    r"^(changelog|changes|history|news|license|licence|copying|notice|third[_-]?party|"
    r"code[_-]of[_-]conduct|security|releasing|release[_-]notes|authors|contributors|"
    r"pull_request_template|issue_template)",
    re.IGNORECASE,
)
# Names that usually explain a project; read these first.
_PREFERRED = re.compile(
    r"(overview|architecture|design|concept|guide|intro|getting[-_ ]?started|workflow|"
    r"manifesto|vision|about|how[-_ ]?it[-_ ]?works|docs?/(index|readme))",
    re.IGNORECASE,
)
# [text](target), <a href="target">, and reference-style [label]: target
_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?[^)]*\)|href=[\"']([^\"']+)[\"']|^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
# Bare mentions such as `docs/architecture.md` or "see MANIFESTO.md"
_BARE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-][\w.-]*\.(?:md|markdown))\b", re.IGNORECASE)


@dataclass(frozen=True)
class BriefingFile:
    path: str
    chars: int
    truncated: bool


@dataclass
class Briefing:
    files: list[BriefingFile] = field(default_factory=list)
    not_read: list[str] = field(default_factory=list)
    text: str = ""  # appended to the system prompt; empty if there was nothing to say

    @property
    def chars(self) -> int:
        return sum(f.chars for f in self.files)


def budget_for(context_tokens: int) -> int:
    """About 20% of the context window, at roughly 4 characters per token."""
    return max(MIN_BUDGET, min(MAX_BUDGET, int(context_tokens * 0.8)))


def build_briefing(ws: Workspace, budget: int) -> Briefing:
    briefing = Briefing()
    sections: list[str] = []
    entry = find_entry(ws)
    queue: deque[tuple[Path, int]] = deque([(entry, 0)] if entry else [])
    seen: set[Path] = set()
    used = 0

    while queue:
        path, depth = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        rel = ws.rel(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        take = min(len(text), PER_FILE, budget - used)
        if depth > 0 and take < len(text) and take < MIN_USEFUL:
            briefing.not_read.append(rel)
            continue
        if take <= 0:
            briefing.not_read.append(rel)
            continue
        shown, note = _cut(text, take, rel)
        used += len(shown)
        briefing.files.append(BriefingFile(rel, len(shown), bool(note)))
        sections.append(f"## File: {rel}\n\n{shown.rstrip()}\n{note}")
        if depth < MAX_DEPTH:
            for ref in references(ws, path, text):
                if ref not in seen:
                    queue.append((ref, depth + 1))

    listing = _top_level(ws)
    if not briefing.files and not listing:
        return briefing
    parts = ["# Project briefing", ""]
    if briefing.files:
        names = ", ".join(f.path for f in briefing.files)
        parts += [
            f"beastron read these project files for you when the session started: {names}. "
            "Their content is below, so you don't need to read them again unless they are cut short.",
            "For questions about the project as a whole, answer from this briefing and read more files "
            "if you need to. Do not guess what the project does from file names.",
            "",
        ]
    if listing:
        parts += [f"Top-level files and folders: {listing}", ""]
    parts += sections
    if briefing.not_read:
        shown = briefing.not_read[:MAX_NOT_READ]
        more = f" (and {len(briefing.not_read) - len(shown)} more)" if len(briefing.not_read) > len(shown) else ""
        parts.append(f"Other documentation linked from these files (not read, read_file if needed): {', '.join(shown)}{more}")
    briefing.text = "\n".join(parts).rstrip() + "\n"
    return briefing


def find_entry(ws: Workspace) -> Path | None:
    """AGENTS.md, else README.md, else the most likely looking other Markdown file."""
    try:
        top = {p.name.lower(): p for p in ws.root.iterdir() if p.is_file()}
    except OSError:
        return None
    for name in ENTRY_NAMES:
        if name in top:
            return top[name]
    candidates = []
    for i, path in enumerate(_walk(ws, ws.root)):
        if i >= MAX_FALLBACK_SCAN:
            break
        if path.suffix.lower() in MARKDOWN and not _SKIP.match(path.name) and not _is_binary(path):
            candidates.append(path)
    if not candidates:
        return None
    return min(candidates, key=lambda p: (len(p.relative_to(ws.root).parts), _rank(ws, p), ws.rel(p)))


def references(ws: Workspace, source: Path, text: str) -> list[Path]:
    """Markdown files inside the project that `source` links to or mentions, best first."""
    targets: list[str] = []
    for m in _LINK.finditer(text):
        targets.append(next(g for g in m.groups() if g))
    targets += [m.group(1) for m in _BARE.finditer(text)]

    found: list[Path] = []
    for target in targets:
        if "://" in target or target.startswith(("mailto:", "#")):
            continue
        target = target.split("#", 1)[0].split("?", 1)[0]
        if not target:
            continue
        bases = [ws.root] if target.startswith("/") else [source.parent, ws.root]
        for base in bases:
            path = (base / target.lstrip("/")).resolve()
            if (
                path.is_relative_to(ws.root)
                and path.suffix.lower() in MARKDOWN
                and path.is_file()
                and not _SKIP.match(path.name)
                and not any(part in IGNORED_DIRS for part in path.relative_to(ws.root).parts)
                and path != source
            ):
                if path not in found:
                    found.append(path)
                break
    return sorted(found, key=lambda p: _rank(ws, p))  # stable: keeps link order within a rank


def _rank(ws: Workspace, path: Path) -> int:
    return 0 if _PREFERRED.search(ws.rel(path)) else 1


def _cut(text: str, take: int, rel: str) -> tuple[str, str]:
    if take >= len(text):
        return text, ""
    cut = text.rfind("\n", 0, take)
    if cut < take * 0.8:
        cut = take
    shown = text[:cut]
    shown_lines = shown.count("\n") + 1
    total_lines = text.count("\n") + 1
    note = (
        f"\n[cut after line {shown_lines} of {total_lines}; "
        f"call read_file on {rel} with offset={shown_lines + 1} for the rest]\n"
    )
    return shown, note


def _top_level(ws: Workspace) -> str:
    try:
        entries = sorted(ws.root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return ""
    names = [p.name + ("/" if p.is_dir() else "") for p in entries if p.name not in IGNORED_DIRS]
    if len(names) > MAX_TOP_LEVEL:
        names = names[:MAX_TOP_LEVEL] + [f"... {len(names) - MAX_TOP_LEVEL} more"]
    return ", ".join(names)
