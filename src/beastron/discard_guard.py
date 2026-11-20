"""Protect changes made during this turn from common destructive Git commands.

This recognizes ordinary shell commands; it is not a shell sandbox or a defense
against intentionally disguised commands. State is compared to the turn's
initial workspace so pre-existing, untouched edits do not activate the guard.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(root: Path, *args: str) -> bytes | None:
    try:
        p = subprocess.run(['git', '-c', 'core.fsmonitor=false', *args], cwd=root,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _fingerprint(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return 'link:' + os.readlink(path)
        if path.is_file():
            h = hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(chunk)
            return h.hexdigest()
    except OSError:
        return '<unreadable>'
    return None


def _snapshot(root: Path) -> tuple[dict[str, str | None], dict[str, bytes], set[str]] | None:
    listed = _git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard')
    index = _git(root, 'ls-files', '-z', '--stage')
    tracked = _git(root, 'ls-files', '-z', '--cached')
    if listed is None or index is None or tracked is None:
        return None
    paths = set(os.fsdecode(p) for p in listed.split(b'\0') if p)
    stages: dict[str, bytes] = {}
    for record in index.split(b'\0'):
        if record and b'\t' in record:
            meta, name = record.split(b'\t', 1)
            key = os.fsdecode(name)
            stages[key] = stages.get(key, b'') + meta + b'\0'
    return ({p: _fingerprint(root / p) for p in paths}, stages,
            {os.fsdecode(p) for p in tracked.split(b'\0') if p})


@dataclass(frozen=True)
class Attempt:
    action: str
    args: tuple[str, ...]
    redirected: bool = False


def attempts(command: str) -> list[Attempt]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(c in ';&|()' for c in token):
            segments.append([])
        else:
            segments[-1].append(token)
    found = []
    for seg in segments:
        if seg[:1] == ['command']:
            seg = seg[1:]
        if not seg or seg[0] != 'git':
            continue
        i, redirected = 1, False
        while i < len(seg) and seg[i].startswith('-'):
            option = seg[i]
            if option in ('-C', '--git-dir', '--work-tree'):
                if i + 1 >= len(seg):
                    break
                redirected |= option != '-C' or seg[i + 1] not in ('.', './')
                i += 2
            elif option.startswith(('--git-dir=', '--work-tree=')) or option.startswith('-C'):
                redirected = True
                i += 1
            elif option in ('-c', '--config-env'):
                i += 2
            else:
                i += 1
        if i >= len(seg):
            continue
        action, args = seg[i], seg[i + 1:]
        stash_push = action == 'stash' and (not args or args[0].startswith('-') or args[0] in ('push', 'save'))
        discard = (stash_push or action in ('restore', 'clean') or
                   action == 'reset' and '--hard' in args or
                   action == 'checkout' and ('--' in args or '--force' in args or '-f' in args))
        if discard:
            found.append(Attempt(action, tuple(args), redirected))
    return found


class DiscardGuard:
    def __init__(self, root: Path):
        self.root = root
        self.before = _snapshot(root)

    def check(self, command: str) -> list[tuple[Attempt, bool, str]]:
        found = attempts(command)
        if not found:
            return []
        if self.before is None:
            return [(a, False, 'no Git workspace snapshot') for a in found]
        now = _snapshot(self.root)
        if now is None:
            return [(a, True, 'cannot inspect workspace changes') for a in found]
        old_files, old_index, old_tracked = self.before
        files, index, tracked = now
        changed = {p for p in old_files.keys() | files.keys() | old_index.keys() | index.keys()
                   if old_files.get(p) != files.get(p) or old_index.get(p) != index.get(p)}
        result = []
        for a in found:
            # Redirected commands cannot be mapped to this workspace reliably.
            if a.redirected:
                result.append((a, bool(changed), 'target repository differs' if changed else 'no changes made in workspace'))
                continue
            eligible = set(changed)
            if a.action == 'clean':
                eligible -= tracked
                eligible &= files.keys()
                if '--dry-run' in a.args or any(x.startswith('-') and not x.startswith('--') and 'n' in x[1:] for x in a.args):
                    eligible.clear()
            elif a.action == 'stash' and not any(x in ('--include-untracked', '--all') or
                                                  x.startswith('-') and not x.startswith('--') and any(c in x[1:] for c in 'ua')
                                                  for x in a.args):
                eligible &= tracked | old_tracked
            elif a.action in ('reset', 'restore') or a.action == 'checkout' and not any(x in a.args for x in ('-f', '--force')):
                eligible &= tracked | old_tracked
            if a.action == 'restore' and ('--staged' in a.args or '-S' in a.args) and not ('--worktree' in a.args or '-W' in a.args):
                eligible &= {p for p in old_index.keys() | index.keys() if old_index.get(p) != index.get(p)}
            paths = self._pathspec(a)
            if paths and eligible:
                matched = _git(self.root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', *paths)
                if matched is not None:
                    eligible &= {os.fsdecode(p) for p in matched.split(b'\0') if p}
            result.append((a, bool(eligible), 'would affect changes made this turn' if eligible else 'no affected changes made this turn'))
        return result

    @staticmethod
    def _pathspec(a: Attempt) -> list[str]:
        args = list(a.args)
        if '--' in args:
            return args[args.index('--') + 1:]
        if a.action == 'restore':
            # Flags with separate values are left broad, to avoid missing paths.
            if any(x in args for x in ('--source', '-s', '--pathspec-from-file')):
                return []
            return [x for x in args if not x.startswith('-')]
        if a.action == 'clean':
            return [x for x in args if not x.startswith('-')]
        return []
