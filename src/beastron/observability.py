"""Passive measurements only: these never influence the agent's decisions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Conservative literal signals, not a semantic test of a claim's truth.
_TEST_COUNT = re.compile(r'(?i)\b(?:(?:tests?\s+(?:run|passed|failed|completed)|failures?|errors?|skipped)\s*[:=]\s*(\d+)|(\d+)\s+(?:tests?\b|passed\b|failed\b|failures?\b|errors?\b))')
_LINES = re.compile(r'^lines (\d+)-(\d+) of \d+$')
_FULL = re.compile(r'^(\d+) lines$')


def test_counts(text: str) -> set[int]:
    return {int(m.group(1) or m.group(2)) for m in _TEST_COUNT.finditer(text)}


@dataclass
class Observer:
    evidence: set[int] = field(default_factory=set)
    reads: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def tool_result(self, name: str, args: dict, output: str, summary: str, ok: bool) -> tuple[bool, bool]:
        self.evidence.update(test_counts(output))
        if not ok or name != 'read_file' or not isinstance(args.get('path'), str):
            if ok and name in ('edit_file', 'write_file', 'delete_file', 'run_shell'):
                # Any shell command may modify files; avoid calling later reads repeats.
                self.reads.clear()
            return False, False
        partial, full = _LINES.match(summary), _FULL.match(summary)
        if partial:
            start, end = map(int, partial.groups())
        elif full:
            start, end = 1, int(full.group(1))
        else:
            return False, False
        path = args['path']
        previous = self.reads.setdefault(path, [])
        overlap = any(start <= old_end and old_start <= end for old_start, old_end in previous)
        previous.append((start, end))
        return end - start + 1 <= 20, overlap

    def unsupported_test_counts(self, text: str) -> int:
        return len(test_counts(text) - self.evidence)
