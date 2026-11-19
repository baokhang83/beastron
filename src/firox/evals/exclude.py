"""Projects the owner never wants used as eval material.

A plain text file, one path per line (`#` starts a comment), at
`~/.config/firox/exclude.txt` or wherever `FIROX_EXCLUDE` points. Commit replay
refuses these repositories, and use-case mining skips their sessions. The file
is local configuration: it never goes into the repository.
"""

from __future__ import annotations

import os
from pathlib import Path


def exclude_file() -> Path:
    return Path(os.environ.get("FIROX_EXCLUDE") or "~/.config/firox/exclude.txt").expanduser()


def excluded_paths() -> list[Path]:
    path = exclude_file()
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(Path(line).expanduser().resolve())
    return entries


def is_excluded(path: Path) -> bool:
    target = path.expanduser().resolve()
    return any(target == entry or target.is_relative_to(entry) for entry in excluded_paths())
