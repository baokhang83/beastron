"""Interactive permission modes; tools still validate their own paths."""

from enum import Enum
import re


class PermissionMode(str, Enum):
    AUTO = "auto"
    ASK = "ask"


AUTO_APPROVED_TOOLS = frozenset({"write_file", "edit_file", "delete_file"})


# Deliberately narrow: only a single find rooted at '.', with no actions,
# redirection, command substitution, shell operators, or arbitrary predicates.
_SIMPLE_FIND = re.compile(
    r"[ \t]*find[ \t]+\.[ \t]+-name[ \t]+(?:\"[A-Za-z0-9_.?*+-]+\"|"
    r"'[A-Za-z0-9_.?*+-]+'|[A-Za-z0-9_.?*+-]+)[ \t]+-type[ \t]+f[ \t]*"
)


def auto_approve(mode: PermissionMode, tool: str, args: dict | None = None) -> bool:
    if mode != PermissionMode.AUTO:
        return False
    if tool in AUTO_APPROVED_TOOLS:
        return True
    command = (args or {}).get("command")
    return tool == "run_shell" and isinstance(command, str) and bool(_SIMPLE_FIND.fullmatch(command))
