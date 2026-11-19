"""Events the agent emits while it works.

The agent never prints. It emits these, and whoever is listening decides what
to do: the Rich UI draws them, an eval runner could record them, a log writer
could save them. Approval is the one thing that needs an answer back, so it is
a separate callback (see ApprovalRequest) rather than an event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ModelStart:
    step: int


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ModelEnd:
    step: int


@dataclass(frozen=True)
class ToolStart:
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolEnd:
    call_id: str
    name: str
    ok: bool
    summary: str
    output: str


@dataclass(frozen=True)
class Nudge:
    """The model announced an action without doing it; the agent asked it to continue."""

    reason: str


@dataclass(frozen=True)
class ContextUpdate:
    used: int
    limit: int


@dataclass(frozen=True)
class ErrorEvent:
    message: str


TurnEndReason = Literal["done", "max_steps", "interrupted", "error"]


@dataclass(frozen=True)
class TurnEnd:
    reason: TurnEndReason
    steps: int
    prompt_tokens: int
    completion_tokens: int
    elapsed: float
    model_seconds: float


Event = (
    ModelStart
    | ReasoningDelta
    | TextDelta
    | ModelEnd
    | ToolStart
    | ToolEnd
    | Nudge
    | ContextUpdate
    | ErrorEvent
    | TurnEnd
)


@dataclass(frozen=True)
class Preview:
    """What the user sees before approving a risky tool call."""

    kind: Literal["diff", "command"]
    title: str
    body: str


@dataclass(frozen=True)
class ApprovalRequest:
    tool: str
    args: dict[str, Any]
    preview: Preview
