from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..events import FileChange, Preview

Args = dict[str, Any]


class ToolError(Exception):
    """A problem the model can fix, reported back to it as the tool result."""


@dataclass(frozen=True)
class ToolResult:
    output: str  # what the model sees
    summary: str  # one short line for the UI
    ok: bool = True
    denied: bool = False  # the user said no at the approval prompt
    change: FileChange | None = None  # set by tools that change a file


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments
    run: Callable[[Args], ToolResult]
    # Tools with a preview need the user's approval. The preview also
    # validates the call, so a call that would fail never reaches the user.
    preview: Callable[[Args], Preview] | None = None

    @property
    def needs_approval(self) -> bool:
        return self.preview is not None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def check_args(self, args: Args) -> Args:
        """Check required arguments and coerce simple types.

        Small models often send "5" for an integer, or forget an argument.
        Unknown extra arguments are ignored.
        """
        props: dict[str, Any] = self.parameters.get("properties", {})
        missing = [k for k in self.parameters.get("required", []) if args.get(k) is None]
        if missing:
            raise ToolError(
                f"Missing required argument(s): {', '.join(missing)}. "
                f"Expected arguments: {', '.join(props)}."
            )
        checked = dict(args)
        for key, value in args.items():
            kind = props.get(key, {}).get("type")
            if kind == "integer" and not isinstance(value, int):
                try:
                    checked[key] = int(value)
                except (TypeError, ValueError):
                    raise ToolError(f"Argument '{key}' must be an integer, got {value!r}.") from None
            elif kind == "string" and not isinstance(value, str):
                raise ToolError(f"Argument '{key}' must be a string, got {type(value).__name__}.")
        return checked
