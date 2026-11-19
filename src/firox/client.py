"""Talking to the model.

ModelClient is the only interface the agent loop depends on. The
OpenAI-compatible implementation below covers Ollama, LM Studio, llama.cpp,
vLLM and OpenRouter. Another provider (for example Claude through Anthropic's
own SDK) would be a second class with the same two methods.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import OpenAI

from .config import ModelProfile
from .quirks import TagRouter, ToolCall, parse_text_tool_call, safe_arguments

Message = dict[str, Any]
OnText = Callable[[str], None]


class ModelError(Exception):
    """The model could not be reached or refused the request."""


@dataclass
class ModelReply:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelClient(Protocol):
    profile: ModelProfile

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_text: OnText,
        on_reasoning: OnText,
    ) -> ModelReply: ...

    def assistant_message(self, reply: ModelReply) -> Message: ...


class OpenAICompatClient:
    def __init__(self, profile: ModelProfile) -> None:
        self.profile = profile
        self._client = OpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key(),
            timeout=600,  # local models can take minutes on a long prompt
            max_retries=1,
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_text: OnText,
        on_reasoning: OnText,
    ) -> ModelReply:
        p = self.profile
        kwargs: dict[str, Any] = {
            "model": p.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if p.temperature is not None:
            kwargs["temperature"] = p.temperature
        if p.reasoning_effort:
            kwargs["reasoning_effort"] = p.reasoning_effort
        if p.extra_body:
            kwargs["extra_body"] = p.extra_body

        reply = ModelReply()
        router = TagRouter()
        text: list[str] = []
        reasoning: list[str] = []
        text_calls: list[str] = []
        slots: list[dict[str, str]] = []

        def route(parts: list[tuple[str, str]]) -> None:
            for channel, piece in parts:
                if channel == "text":
                    text.append(piece)
                    on_text(piece)
                elif channel == "think":
                    reasoning.append(piece)
                    on_reasoning(piece)
                else:
                    text_calls.append(piece)

        try:
            for chunk in self._client.chat.completions.create(**kwargs):
                if chunk.usage:
                    reply.prompt_tokens = chunk.usage.prompt_tokens
                    reply.completion_tokens = chunk.usage.completion_tokens
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # Not part of the OpenAI spec: Ollama and OpenRouter use
                # "reasoning", vLLM and DeepSeek use "reasoning_content".
                extra = delta.model_extra or {}
                piece = extra.get("reasoning") or extra.get("reasoning_content")
                if piece:
                    reasoning.append(piece)
                    on_reasoning(piece)
                if delta.content:
                    route(router.feed(delta.content))
                for tc in delta.tool_calls or []:
                    _merge_tool_call_delta(slots, tc)
        except openai.APIConnectionError as e:
            raise ModelError(
                f"Cannot reach {p.base_url}. Is the server running? (Ollama: `ollama serve`)"
            ) from e
        except openai.NotFoundError as e:
            raise ModelError(
                f"Model '{p.model}' not found on {p.base_url}. (Ollama: `ollama pull {p.model}`)"
            ) from e
        except openai.APIStatusError as e:
            raise ModelError(f"Server returned {e.status_code}: {e.message}") from e
        route(router.flush())

        reply.content = "".join(text).strip()
        reply.reasoning = "".join(reasoning).strip()
        reply.tool_calls = [
            ToolCall(s["id"] or f"call_{i}", s["name"], s["arguments"]) for i, s in enumerate(slots)
        ]
        if not reply.tool_calls and text_calls:
            # Quirk: the model wrote its tool calls as text.
            reply.tool_calls = [parse_text_tool_call(b, i) for i, b in enumerate(text_calls)]
        return reply

    def assistant_message(self, reply: ModelReply) -> Message:
        msg: Message = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": safe_arguments(c.arguments)},
                }
                for c in reply.tool_calls
            ]
        if "send_reasoning_back" in self.profile.quirks and reply.reasoning:
            msg["reasoning"] = reply.reasoning
        return msg


def _merge_tool_call_delta(slots: list[dict[str, str]], tc: Any) -> None:
    """Accumulate one streamed tool-call fragment.

    OpenAI streams a call in pieces keyed by index: the id and name first, then
    the arguments a few characters at a time. Ollama sends each call whole. This
    handles both, plus servers that leave out the index.
    """
    index = tc.index
    if index is None:
        is_new = not slots or (tc.id and all(s["id"] != tc.id for s in slots))
        index = len(slots) if is_new else len(slots) - 1
    while len(slots) <= index:
        slots.append({"id": "", "name": "", "arguments": ""})
    slot = slots[index]
    if tc.id:
        slot["id"] = tc.id
    if tc.function:
        if tc.function.name and not slot["name"]:
            slot["name"] = tc.function.name
        if tc.function.arguments:
            slot["arguments"] += tc.function.arguments
