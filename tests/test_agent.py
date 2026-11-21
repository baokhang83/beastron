"""Agent loop tests with a scripted fake model (no server needed)."""

from beastron.agent import Agent
from beastron.client import ModelReply, OpenAICompatClient
from beastron.config import ModelProfile
from beastron.events import ToolEnd, TurnEnd
from beastron.quirks import ToolCall
from beastron.tools import Workspace, make_tools

PROFILE = ModelProfile(name="fake", base_url="http://unused", model="fake", context=1000)


class FakeClient:
    """Returns scripted replies and records what the agent sent."""

    def __init__(self, replies):
        self.profile = PROFILE
        self.replies = list(replies)
        self.seen = []

    def complete(self, messages, tools, on_text, on_reasoning):
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if reply.content:
            on_text(reply.content)
        return reply

    # Reuse the real message-building logic.
    assistant_message = OpenAICompatClient.assistant_message


def call(name, arguments, id="c1"):
    return ModelReply(tool_calls=[ToolCall(id, name, arguments)], prompt_tokens=10, completion_tokens=5)


def make_agent(tmp_path, replies, approve=lambda req: True, max_steps=25):
    (tmp_path / "hello.txt").write_text("hi there\n")
    events = []
    ws = Workspace(tmp_path)
    client = FakeClient(replies)
    agent = Agent(client, ws, make_tools(ws), on_event=events.append, approve=approve, max_steps=max_steps)
    return agent, client, events


def tool_results(agent):
    return [m["content"] for m in agent.messages if m["role"] == "tool"]


def test_tool_call_then_answer(tmp_path):
    agent, client, events = make_agent(
        tmp_path, [call("read_file", '{"path": "hello.txt"}'), ModelReply(content="It says hi.")]
    )
    assert agent.run("What is in hello.txt?") == "done"
    assert tool_results(agent) == ["hi there\n"]
    assert agent.messages[-1] == {"role": "assistant", "content": "It says hi."}
    end = events[-1]
    assert isinstance(end, TurnEnd) and end.steps == 2 and end.prompt_tokens == 10


def test_bad_json_is_reported_to_the_model(tmp_path):
    agent, client, _ = make_agent(tmp_path, [call("read_file", '{"path": '), ModelReply(content="ok")])
    agent.run("go")
    assert "not valid JSON" in tool_results(agent)[0]
    # the malformed arguments are not sent back to the server
    sent_call = client.seen[1][-2]["tool_calls"][0]
    assert sent_call["function"]["arguments"] == "{}"


def test_unknown_tool(tmp_path):
    agent, _, _ = make_agent(tmp_path, [call("delete_everything", "{}"), ModelReply(content="ok")])
    agent.run("go")
    assert "no tool named 'delete_everything'" in tool_results(agent)[0]


def test_denial_ends_the_turn(tmp_path):
    agent, client, events = make_agent(
        tmp_path,
        [call("run_shell", '{"command": "rm -rf /"}'), ModelReply(content="I'll try again")],
        approve=lambda req: False,
    )
    assert agent.run("go") == "denied"
    assert len(client.seen) == 1  # the model is not asked again
    assert "denied" in tool_results(agent)[0]
    assert any(isinstance(e, ToolEnd) and e.summary == "denied" for e in events)


def test_calls_after_a_denial_are_skipped(tmp_path):
    reply = ModelReply(
        tool_calls=[
            ToolCall("c1", "run_shell", '{"command": "ls"}'),
            ToolCall("c2", "write_file", '{"path": "x.txt", "content": "x"}'),
        ]
    )
    asked = []
    agent, _, _ = make_agent(tmp_path, [reply], approve=lambda req: asked.append(req.tool) or False)
    assert agent.run("go") == "denied"
    assert asked == ["run_shell"]  # never asked about write_file
    assert "Not run" in tool_results(agent)[1]
    assert not (tmp_path / "x.txt").exists()


def test_invalid_edit_never_asks_for_approval(tmp_path):
    asked = []
    agent, _, _ = make_agent(
        tmp_path,
        [call("edit_file", '{"path": "hello.txt", "old_text": "nope", "new_text": "x"}'), ModelReply(content="ok")],
        approve=lambda req: asked.append(req) or True,
    )
    agent.run("go")
    assert asked == []
    assert "not found" in tool_results(agent)[0]


def test_step_limit(tmp_path):
    replies = [call("list_files", "{}", id=f"c{i}") for i in range(3)]
    agent, _, events = make_agent(tmp_path, replies, max_steps=3)
    assert agent.run("loop forever") == "max_steps"


def test_announced_but_not_taken_action_gets_a_nudge(tmp_path):
    agent, client, events = make_agent(
        tmp_path,
        [
            ModelReply(content="The bug is in add(). Let's fix it and run the tests."),
            call("read_file", '{"path": "hello.txt"}'),
            ModelReply(content="Done: fixed and verified."),
        ],
    )
    assert agent.run("fix it") == "done"
    assert len(client.seen) == 3
    assert any(m["content"].startswith("[beastron]") for m in agent.messages if m["role"] == "user")


def test_nudges_are_limited(tmp_path):
    replies = [ModelReply(content="Let me look at it.") for _ in range(5)]
    agent, client, _ = make_agent(tmp_path, replies)
    assert agent.run("go") == "done"
    assert len(client.seen) == 3  # the first reply plus two nudges


def test_final_summary_is_not_nudged(tmp_path):
    agent, client, _ = make_agent(tmp_path, [ModelReply(content="Fixed the bug; all 3 tests pass.")])
    agent.run("go")
    assert len(client.seen) == 1


def test_an_empty_reply_with_the_answer_in_the_reasoning_gets_a_nudge(tmp_path):
    agent, client, events = make_agent(
        tmp_path,
        [
            ModelReply(content="", reasoning="The answer: add a --verbose flag to the parser."),
            ModelReply(content="Add a --verbose flag to the parser."),
        ],
    )
    assert agent.run("what would it take?") == "done"
    assert len(client.seen) == 2
    nudge = agent.messages[-2]
    assert nudge["role"] == "user" and "Your last reply was empty" in nudge["content"]
    assert agent.messages[-1]["content"] == "Add a --verbose flag to the parser."


def test_an_empty_reply_without_reasoning_is_nudged(tmp_path):
    agent, client, events = make_agent(tmp_path, [ModelReply(content=""), ModelReply(content="done")])
    assert agent.run("go") == "done"
    assert len(client.seen) == 2
    assert client.seen[1][-1]["content"] == (
        "[beastron] Your last reply was empty. If there is more to do, call a tool now. "
        "If the task is complete, reply with your final summary."
    )
    assert any(getattr(e, "reason", None) == "the reply was completely empty" for e in events)


def test_completely_empty_replies_share_two_nudge_limit(tmp_path):
    agent, client, _ = make_agent(tmp_path, [ModelReply(content="") for _ in range(4)])
    assert agent.run("go") == "done"
    assert len(client.seen) == 3


def test_empty_content_with_tool_call_is_not_empty_reply(tmp_path):
    agent, client, events = make_agent(tmp_path, [call("read_file", '{"path": "hello.txt"}'), ModelReply(content="done")])
    assert agent.run("go") == "done"
    assert len(client.seen) == 2
    assert not any(getattr(e, "reason", None) == "the reply was completely empty" for e in events)


def test_empty_reply_nudges_share_the_limit(tmp_path):
    replies = [ModelReply(content="", reasoning="thinking only") for _ in range(5)]
    agent, client, _ = make_agent(tmp_path, replies)
    assert agent.run("go") == "done"
    assert len(client.seen) == 3  # the first reply plus two nudges


def calls(*specs):
    """One model reply with several tool calls: specs are (name, arguments) pairs."""
    return ModelReply(tool_calls=[ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(specs)])


def test_turn_end_reports_real_changes(tmp_path):
    agent, _, events = make_agent(
        tmp_path,
        [
            calls(
                ("write_file", '{"path": "new.py", "content": "a\\nb\\n"}'),
                ("edit_file", '{"path": "new.py", "old_text": "b", "new_text": "c"}'),
                ("edit_file", '{"path": "hello.txt", "old_text": "hi", "new_text": "hello"}'),
                ("write_file", '{"path": "tmp.py", "content": "x"}'),
                ("delete_file", '{"path": "tmp.py"}'),
                ("run_shell", '{"command": "true"}'),
            ),
            ModelReply(content="I rewrote the whole project and verified everything."),
        ],
    )
    agent.run("go")
    end = events[-1]
    assert isinstance(end, TurnEnd)
    record = {c.path: (c.kind, c.added, c.removed) for c in end.changes}
    assert record == {
        "new.py": ("created", 3, 1),  # created, then edited: still a new file
        "hello.txt": ("modified", 1, 1),
        # tmp.py was created and deleted again: no net change, not listed
    }
    assert end.shell_commands == 1


def test_changes_reset_each_turn(tmp_path):
    agent, _, events = make_agent(
        tmp_path,
        [
            call("write_file", '{"path": "a.py", "content": "x"}'),
            ModelReply(content="done"),
            ModelReply(content="nothing to do"),
        ],
    )
    agent.run("first")
    agent.run("second")
    assert events[-1].changes == () and events[-1].shell_commands == 0


def test_system_prompt_contains_the_briefing(tmp_path):
    (tmp_path / "README.md").write_text("FluencyLoop captures the decisions behind each feature.")
    agent, client, _ = make_agent(tmp_path, [ModelReply(content="ok")])
    agent.run("what is this project about?")
    system = client.seen[0][0]["content"]
    assert "FluencyLoop captures the decisions behind each feature." in system
    assert [f.path for f in agent.briefing.files] == ["README.md"]


def test_interrupt_repairs_history(tmp_path):
    def interrupt(req):
        raise KeyboardInterrupt

    agent, _, _ = make_agent(tmp_path, [call("run_shell", '{"command": "ls"}')], approve=interrupt)
    assert agent.run("go") == "interrupted"
    assert agent.messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": "Interrupted by the user."}


def test_raw_tool_markup_is_nudged_then_tool_can_run(tmp_path):
    agent, client, events = make_agent(tmp_path, [
        ModelReply(content='Let me read it. <function=read_file></tool_call>'),
        call('read_file', '{"path": "hello.txt"}'), ModelReply(content='It says hi.'),
    ])
    assert agent.run('read it') == 'done'
    assert client.seen[1][-1]['content'] == (
        '[beastron] Your response contained raw tool-call markup as text instead of executing a tool. '
        'Call the tool directly now.'
    )
    assert tool_results(agent) == ['hi there\n']
    assert any('raw tool-call markup' in getattr(e, 'reason', '') for e in events if getattr(e, 'reason', None))


def test_raw_markup_shares_existing_two_nudge_limit(tmp_path):
    agent, client, _ = make_agent(tmp_path, [ModelReply(content=''), ModelReply(content='</tool_call>'),
                                              ModelReply(content='<tool_call>')])
    assert agent.run('go') == 'done'
    assert len(client.seen) == 3


def test_real_tool_calls_are_not_nudged_even_if_content_has_markup(tmp_path):
    reply = call('read_file', '{"path": "hello.txt"}')
    reply.content = '<tool_call>'
    agent, client, _ = make_agent(tmp_path, [reply, ModelReply(content='done')])
    assert agent.run('go') == 'done'
    assert len(client.seen) == 2 and tool_results(agent) == ['hi there\n']


def test_agent_uses_current_profile_step_limit_unless_explicitly_overridden(tmp_path):
    from dataclasses import replace
    ws = Workspace(tmp_path)
    client = FakeClient([call('list_files', '{}') for _ in range(2)])
    client.profile = replace(PROFILE, max_steps=2)
    agent = Agent(client, ws, make_tools(ws), lambda e: None, lambda r: True, briefing=False)
    assert agent.run('go') == 'max_steps' and len(client.seen) == 2
    client.profile = replace(PROFILE, max_steps=1)
    client.replies = [call('list_files', '{}')]
    assert agent.run('go') == 'max_steps' and len(client.seen) == 3


def test_raw_markup_is_nudged_only_once_per_turn(tmp_path):
    agent, client, _ = make_agent(tmp_path, [ModelReply(content='<tool_call>'),
                                              ModelReply(content='<function=read_file>')])
    assert agent.run('go') == 'done'
    assert len(client.seen) == 2
