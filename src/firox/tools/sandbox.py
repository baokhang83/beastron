"""Keeps file tools inside the project folder.

This covers the file tools only. run_shell can do anything your user account
can, which is why every shell command needs your approval. For full isolation,
run firox inside a container.
"""

from __future__ import annotations

from pathlib import Path

from .base import ToolError


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, path: str, *, for_write: bool = False) -> Path:
        # resolve() follows symlinks, so a link pointing outside is caught too.
        target = (self.root / Path(path).expanduser()).resolve()
        if not target.is_relative_to(self.root):
            raise ToolError(
                f"'{path}' is outside the project folder ({self.root}). "
                "Only paths inside it are allowed."
            )
        if for_write and ".git" in target.relative_to(self.root).parts:
            raise ToolError("Refusing to modify files inside .git/. Use git commands instead.")
        return target

    def rel(self, path: Path) -> str:
        rel = path.relative_to(self.root).as_posix()
        return rel or "."
