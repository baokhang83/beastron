"""The agent loop: ask the model, run the tools it asks for, repeat until done."""

from __future__ import annotations

import json
import platform
import re
import time
from collections.abc import Callable
from datetime import date

from .briefing import Briefing, budget_for, build_briefing
from .client import Message, ModelClient, ModelError
from .events import (
    ApprovalRequest,
    ContextUpdate,
    ErrorEvent,
    Event,
    FileChange,
    ModelEnd,
    ModelStart,
    Nudge,
    ReasoningDelta,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnEndReason,
    TurnStart,
)
from .quirks import ToolCall
from .tools import Tool, ToolError, ToolResult, Workspace

MAX_TOOL_OUTPUT = 20_000  # characters; a last safety net, tools clip their own output first
MAX_NUDGES = 2  # per user message
DENIED_MESSAGE = (
    "The user denied this action. Your turn is over; the user will tell you what to do next. "
    "Do not repeat this action unless they ask for it."
)
SKIPPED_AFTER_DENIAL = "Not run: the user denied an earlier action in this reply."

# Small models often end a reply with "Let's fix it..." and no tool call. A
# reply without a tool call ends the turn, so the work never happens. When the
# end of a reply announces a next step, the agent asks the model to continue.
_ANNOUNCES_ACTION = re.compile(
    r"\b(I'll|I will|I'm going to|I am going to|Let me|Let's|Let us|Next,? I|Now I)\b",
    re.IGNORECASE,
)
NUDGE_MESSAGE = (
    "[beastron] Your last reply described a next step but did not call a tool, so nothing happened. "
    "If there is more to do, call the tool now. If the task is complete, reply with your final summary."
)

# With thinking on, some models (qwen3.5:9b was seen doing it) end a turn with the whole
# answer in the reasoning channel and nothing in the reply: the user, and any check on the
# final answer, see an empty reply. The agent asks the model to write the answer as a reply.
EMPTY_REPLY_MESSAGE = (
    "[beastron] Your last reply was empty: your answer was only in your reasoning, which the user does not see. "
    "Write your final answer now as a normal reply."
)


def announces_unfinished_action(text: str) -> bool:
    tail = text.strip()[-300:]
    return bool(tail) and bool(_ANNOUNCES_ACTION.search(tail))


def merge_change(changes: dict[str, FileChange], new: FileChange) -> None:
    """Fold a file change into the turn's record, one entry per path."""
    old = changes.get(new.path)
    if old is None:
        changes[new.path] = new
        return
    if old.kind == "created" and new.kind == "deleted":
        del changes[new.path]  # created and removed again: no net change
        return
    if old.kind == "created":
        kind = "created"
    elif old.kind == "deleted" and new.kind == "created":
        kind = "modified"  # deleted, then written again
    else:
        kind = new.kind
    changes[new.path] = FileChange(new.path, kind, old.added + new.added, old.removed + new.removed)


SYSTEM_PROMPT = """\
You are beastron, a coding agent. You work on the project in {root} using the tools provided.

How to work:
- If the user asks a question ("how does...", "what would it take to...", "why..."), answer it.
  Look at the code if that helps, but do not change any file unless the user asks you to make the change.
- Explore before changing anything: use list_files and search_code to find the relevant code, then read_file.
- When the request is about recent work ("my latest changes", "what I just added", "undo my last change"),
  start with the git tool: status lists uncommitted changes and untracked (brand-new) files, diff shows the
  changes, log and show show recent commits.
- Always read a file before editing it. Use edit_file for changes to existing files; old_text must be copied exactly from the file.
- Use write_file only to create new files or when replacing a whole file is clearly simpler. Use delete_file to remove a file.
- After changing code, verify it with run_shell (run the tests, or run the program).
- If a tool returns an error, read it carefully and fix your next call instead of repeating the same one.
- Use paths relative to the project root.
- Never announce an action ("I'll fix it", "Let me run the tests") and then stop: call the tool in that same reply.
  A reply without a tool call ends your turn, so only reply without one when the task is finished.
- When the task is done, reply with a short summary of what you changed and how you verified it.
  Only mention changes you actually made with a tool; beastron shows the user its own list of changed files.
- If the request is unclear, ask a question instead of guessing.

Environment: {os}, today is {today}.
"""

Approver = Callable[[ApprovalRequest], bool]


class Agent:
    def __init__(
        self,
        client: ModelClient,
        workspace: Workspace,
        tools: list[Tool],
        on_event: Callable[[Event], None],
        approve: Approver,
        max_steps: int = 25,
        briefing: bool = True,
    ) -> None:
        self.client = client
        self.workspace = workspace
        self.use_briefing = briefing
        self.briefing = Briefing()
        self.tools = {t.name: t for t in tools}
        self.emit = on_event
        self.approve = approve
        self.max_steps = max_steps
        self.messages: list[Message] = []
        self.context_used = 0
        self._changes: dict[str, FileChange] = {}
        self._shell_commands = 0
        self.reset()

    def reset(self) -> None:
        system = SYSTEM_PROMPT.format(
            root=self.workspace.root,
            os=f"{platform.system()} {platform.release()}",
            today=date.today().isoformat(),
        )
        # Read the project's docs up front, so the model knows what the project
        # is before its first step instead of having to think of opening them.
        if self.use_briefing:
            self.briefing = build_briefing(self.workspace, budget_for(self.client.profile.context))
            if self.briefing.text:
                system += "\n" + self.briefing.text
        self.messages = [{"role": "system", "content": system}]
        self.context_used = 0

    def run(self, task: str) -> TurnEndReason:
        """Work on one user message until the model stops calling tools."""
        started = time.monotonic()
        model_seconds = 0.0
        prompt_tokens = completion_tokens = 0
        step = 0
        nudges = 0
        self.emit(TurnStart(task))
        self.messages.append({"role": "user", "content": task})
        schemas = [t.schema() for t in self.tools.values()]
        self._changes: dict[str, FileChange] = {}
        self._shell_commands = 0

        def finish(reason: TurnEndReason) -> TurnEndReason:
            self.emit(
                TurnEnd(
                    reason, step, prompt_tokens, completion_tokens, time.monotonic() - started, model_seconds,
                    changes=tuple(self._changes.values()), shell_commands=self._shell_commands,
                )
            )
            return reason

        try:
            for step in range(1, self.max_steps + 1):
                self.emit(ModelStart(step))
                t0 = time.monotonic()
                reply = self.client.complete(
                    self.messages,
                    schemas,
                    on_text=lambda s: self.emit(TextDelta(s)),
                    on_reasoning=lambda s: self.emit(ReasoningDelta(s)),
                )
                model_seconds += time.monotonic() - t0
                self.emit(ModelEnd(step))

                prompt_tokens += reply.prompt_tokens or 0
                completion_tokens += reply.completion_tokens or 0
                self.messages.append(self.client.assistant_message(reply))
                self._update_context(reply.prompt_tokens, reply.completion_tokens)

                if not reply.tool_calls:
                    if nudges < MAX_NUDGES and not reply.content.strip() and reply.reasoning.strip():
                        nudges += 1
                        self.emit(Nudge("the reply was empty: the answer was only in the reasoning"))
                        self.messages.append({"role": "user", "content": EMPTY_REPLY_MESSAGE})
                        continue
                    if nudges < MAX_NUDGES and announces_unfinished_action(reply.content):
                        nudges += 1
                        self.emit(Nudge("the model announced a step without taking it"))
                        self.messages.append({"role": "user", "content": NUDGE_MESSAGE})
                        continue
                    return finish("done")
                # A denial ends the turn: the user said no, so control goes back
                # to them instead of letting the model try again on its own.
                denied = False
                for call in reply.tool_calls:
                    if denied:
                        output = SKIPPED_AFTER_DENIAL
                    else:
                        output, denied = self._execute(call)
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
                if denied:
                    return finish("denied")
            return finish("max_steps")
        except KeyboardInterrupt:
            self._repair_history()
            return finish("interrupted")
        except ModelError as e:
            self.emit(ErrorEvent(str(e)))
            return finish("error")

    def _execute(self, call: ToolCall) -> tuple[str, bool]:
        """Run one tool call. Returns (output for the model, whether the user denied it)."""
        tool = self.tools.get(call.name)
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as e:
            args = None
            parse_error = f"Error: the arguments are not valid JSON ({e}). Send the arguments as a JSON object."
        else:
            parse_error = None if isinstance(args, dict) else "Error: the arguments must be a JSON object."

        self.emit(ToolStart(call.id, call.name, args if isinstance(args, dict) else {"raw": call.arguments}))

        if tool is None:
            result = ToolResult(
                f"Error: there is no tool named '{call.name}'. Available tools: {', '.join(self.tools)}.",
                "unknown tool",
                ok=False,
            )
        elif parse_error:
            result = ToolResult(parse_error, "invalid arguments", ok=False)
        else:
            result = self._run_tool(tool, args)

        if result.change:
            merge_change(self._changes, result.change)
        self.emit(ToolEnd(call.id, call.name, result.ok, result.summary, result.output))
        output = result.output
        if len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + f"\n[... output cut at {MAX_TOOL_OUTPUT} characters]"
        return output, result.denied

    def _run_tool(self, tool: Tool, args: dict) -> ToolResult:
        try:
            args = tool.check_args(args)
            if tool.preview is not None:
                preview = tool.preview(args)  # also validates; raises ToolError on a bad call
                if not self.approve(ApprovalRequest(tool.name, args, preview)):
                    return ToolResult(DENIED_MESSAGE, "denied", ok=False, denied=True)
            if tool.name == "run_shell":
                self._shell_commands += 1  # its file changes are invisible to the change record
            return tool.run(args)
        except ToolError as e:
            return ToolResult(f"Error: {e}", str(e).split(". ")[0][:80], ok=False)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # a bug in a tool must not kill the session
            return ToolResult(f"Error: {type(e).__name__}: {e}", f"{type(e).__name__}", ok=False)

    def _repair_history(self) -> None:
        """After Ctrl-C, make sure every tool call in the history has a result.

        The API rejects a conversation where an assistant tool call has no
        matching tool message, so the next request would fail without this.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant":
                for call in msg.get("tool_calls", []):
                    if call["id"] not in answered:
                        self.messages.append(
                            {"role": "tool", "tool_call_id": call["id"], "content": "Interrupted by the user."}
                        )
                break

    def _update_context(self, prompt_tokens: int | None, completion_tokens: int | None) -> None:
        if prompt_tokens is not None:
            self.context_used = prompt_tokens + (completion_tokens or 0)
        else:  # server didn't report usage: rough estimate, ~4 characters per token
            self.context_used = len(json.dumps(self.messages)) // 4
        self.emit(ContextUpdate(self.context_used, self.client.profile.context))
