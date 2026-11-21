"""Fixes for the ways different models deviate from the standard API.

Everything model-specific lives here, so the agent loop never needs
`if model == ...` checks.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, exactly as the model produced it


class TagRouter:
    """Splits streamed text into normal text, <think> reasoning and <tool_call> blocks.

    Some servers leave a model's reasoning inline as <think>...</think>, and some
    models write tool calls as <tool_call>{json}</tool_call> text instead of using
    the API's tool_calls field. Tags can be split across stream chunks, so a
    partial tag at the end of a chunk is held back until the next one arrives.

    feed() returns (channel, text) pairs where channel is "text", "think" or
    "tool_call". A "tool_call" pair is always one complete block.
    """

    TAGS = ("think", "tool_call")

    def __init__(self) -> None:
        self._buf = ""
        self._inside: str | None = None
        self._block = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buf += chunk
        out: list[tuple[str, str]] = []
        while self._buf:
            if self._inside is None:
                opens = [(self._buf.find(f"<{t}>"), t) for t in self.TAGS]
                opens = [(i, t) for i, t in opens if i != -1]
                if opens:
                    i, tag = min(opens)
                    if i:
                        out.append(("text", self._buf[:i]))
                    self._buf = self._buf[i + len(tag) + 2 :]
                    self._inside = tag
                    continue
                keep = _partial_suffix(self._buf, [f"<{t}>" for t in self.TAGS])
                emit = self._buf[: len(self._buf) - keep]
                if emit:
                    out.append(("text", emit))
                self._buf = self._buf[len(emit) :]
                break
            close = f"</{self._inside}>"
            i = self._buf.find(close)
            if i != -1:
                self._emit_inside(self._buf[:i], out, closing=True)
                self._buf = self._buf[i + len(close) :]
                self._inside = None
                continue
            keep = _partial_suffix(self._buf, [close])
            self._emit_inside(self._buf[: len(self._buf) - keep], out, closing=False)
            self._buf = self._buf[len(self._buf) - keep :]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        rest, self._buf = self._buf, ""
        if self._inside is None:
            if rest:
                out.append(("text", rest))
        else:
            # Unclosed tag at the end of the reply: keep what we have.
            self._emit_inside(rest, out, closing=True)
            self._inside = None
        return out

    def _emit_inside(self, text: str, out: list[tuple[str, str]], *, closing: bool) -> None:
        if self._inside == "tool_call":
            self._block += text
            if closing:
                out.append(("tool_call", self._block))
                self._block = ""
        elif text:
            out.append((self._inside or "text", text))


def _partial_suffix(text: str, tags: list[str]) -> int:
    """Length of the longest end of `text` that could be the start of a tag."""
    best = 0
    for tag in tags:
        for n in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:n]):
                best = max(best, n)
                break
    return best


def parse_text_tool_call(block: str, index: int) -> ToolCall:
    """Turn a <tool_call> text block into a ToolCall.

    Expected shape: {"name": "...", "arguments": {...}}. If it can't be parsed we
    still return a call, with the raw text as arguments, so the agent reports the
    problem back to the model instead of silently dropping it.
    """
    call_id = f"text_call_{index}"
    try:
        data = json.loads(block.strip())
    except json.JSONDecodeError:
        return ToolCall(call_id, "invalid_tool_call", block.strip())
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return ToolCall(call_id, "invalid_tool_call", block.strip())
    args = data.get("arguments", data.get("parameters", {}))
    if not isinstance(args, str):
        args = json.dumps(args)
    return ToolCall(call_id, data["name"], args)


def safe_arguments(arguments: str) -> str:
    """Arguments to store in the conversation history.

    Ollama parses tool-call arguments in the history as JSON, so one malformed
    call would make every later request fail. Invalid JSON is replaced with {};
    the model still sees the tool's error message explaining what went wrong.
    """
    try:
        json.loads(arguments or "{}")
        return arguments or "{}"
    except json.JSONDecodeError:
        return "{}"


def recover_function_markup(text: str, tools: list[dict]) -> tuple[str, ToolCall] | None:
    """Recover one unambiguous function block, or leave malformed text untouched.

    The caller must opt in. The available tool schemas constrain names and
    parameters; normal agent validation and approvals still apply afterwards.
    """
    import re

    match = re.search(r'(?m)^\s*<function=([A-Za-z_][A-Za-z0-9_]*)>', text)
    if not match or text[:match.start()].count('```') % 2:
        return None
    prefix = text[:match.start()].strip()
    tail = text[match.end():]
    end = re.fullmatch(r'(.*?)</function>\s*</tool_call>\s*', tail, re.DOTALL)
    if not end:
        return None
    name = match.group(1)
    schemas = [t.get('function', {}) for t in tools if t.get('type') == 'function']
    schema = next((f for f in schemas if f.get('name') == name), None)
    if schema is None:
        return None
    params = schema.get('parameters', {})
    properties = params.get('properties', {})
    remaining = end.group(1).strip()
    args = {}
    while remaining:
        part = re.match(r'<parameter=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</parameter>\s*', remaining, re.DOTALL)
        if not part:
            return None
        key, value = part.groups()
        if key not in properties or key in args or '<function=' in value or '<parameter=' in value:
            return None
        value = value.removeprefix('\n').removesuffix('\n')
        kind = properties[key].get('type')
        if kind != 'string':
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
            expected = {'integer': int, 'number': (int, float), 'boolean': bool,
                        'array': list, 'object': dict}.get(kind)
            if expected is None or not isinstance(value, expected) or (kind in ('integer', 'number') and isinstance(value, bool)):
                return None
        args[key] = value
        remaining = remaining[part.end():]
    if any(key not in args for key in params.get('required', [])):
        return None
    return prefix, ToolCall(f'recovered_{uuid.uuid4().hex}', name, json.dumps(args))
