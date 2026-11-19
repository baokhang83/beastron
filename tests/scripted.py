"""A scripted model for end-to-end tests of the eval harness, with no server and no network.

Each script is a list of replies, chosen by a phrase from the task's prompt.
A new client is created for every run, so every run replays its script from
the start, the way the runner creates a new model client per run.
"""

import json

from firox.client import ModelReply, OpenAICompatClient
from firox.config import ModelProfile
from firox.quirks import ToolCall

PROFILE = ModelProfile(name="scripted", base_url="http://unused.invalid/v1", model="scripted-model", context=32768)


def tool(name, **args):
    return ModelReply(tool_calls=[ToolCall(f"call_{name}", name, json.dumps(args))], prompt_tokens=100, completion_tokens=20)


def say(text):
    return ModelReply(content=text, prompt_tokens=100, completion_tokens=20)


# What a capable model would do on two seed tasks ...
FIX_OFF_BY_ONE = [
    tool("read_file", path="utils.py"),
    tool("edit_file", path="utils.py", old_text="range(0, len(xs) - 1, size)", new_text="range(0, len(xs), size)"),
    say("Fixed the range in chunk(): it stopped one element early."),
]
ANSWER_WITHOUT_EDITING = [
    tool("read_file", path="todo.py"),
    say("Add parser.add_argument('--verbose', action='store_true') to the argparse parser in main(), then use args.verbose."),
]
# ... and the known small-model failure: editing when asked a question.
EDIT_INSTEAD_OF_ANSWERING = [
    tool("read_file", path="todo.py"),
    tool("edit_file", path="todo.py", old_text='parser = argparse.ArgumentParser(prog="todo")',
         new_text='parser = argparse.ArgumentParser(prog="todo")\n    parser.add_argument("--verbose", action="store_true")'),
    say("I added a --verbose option to the argparse parser."),
]


class ScriptedClient:
    profile = PROFILE
    assistant_message = OpenAICompatClient.assistant_message  # the real message format

    def __init__(self, scripts):
        self.scripts = scripts
        self.replies = None

    def complete(self, messages, tools, on_text, on_reasoning):
        if self.replies is None:
            task = next(m["content"] for m in messages if m["role"] == "user")
            self.replies = list(next(script for phrase, script in self.scripts.items() if phrase in task))
        reply = self.replies.pop(0) if self.replies else say("Done.")
        if reply.content:
            on_text(reply.content)
        return reply


def factory(scripts):
    """A client factory for the runner: a fresh scripted client per run."""
    return lambda profile: ScriptedClient(scripts)
