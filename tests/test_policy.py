"""Which requests are questions, and what a question may use."""

from pathlib import Path

import pytest

from beastron.evals.task import load_tasks
from beastron.policy import QUESTION, READ_ONLY_TOOLS, WORK, classify
from beastron.quirks import ToolCall
from beastron.client import ModelReply
from test_agent import make_agent, call

TASKS = Path(__file__).resolve().parent.parent / "evals" / "tasks"
# Two tasks are questions although they sit in the git category.
GIT_QUESTIONS = {"git-find-breaking-commit", "git-history-question"}


@pytest.mark.parametrize("text", [
    "How does the cache decide which tests to run?",
    "What would it take to add a --verbose option to the todo command-line tool?",
    "Where is the HTTP request timeout configured, and what is its default value?",
    "Can you summarize what this project is about?",
    "Explain how the retry logic works.",
    "Which commit broke test_totals.py? Give the hash.",
    "Why does checkout fail with a KeyError?\n\nTraceback (most recent call last):\n  File \"checkout.py\", line 4, in checkout\n    rate = TAX[country]\nKeyError: 'CH'",
    "Is there a way to make this faster?",
])
def test_questions(text):
    assert classify(text) == QUESTION


@pytest.mark.parametrize("text", [
    "Fix the failing test in test_utils.py.",
    "Can you fix the failing test?",
    "Could you please add logging to the importer?",
    "Why does this fail? Fix it.",
    "What is wrong here? Fix it and run the tests.",
    "Run the tests and tell me which fail.",
    "Rename calcTot to calculateTotal everywhere.",
    "Add a section for 1.1.0 to CHANGELOG.md.",
    "Let's refactor the validator.",
    "Skip java.lang.invoke.* classes in the transform.",
    "The tests fail. Find the bug, fix it, and run the tests.",
    "Undo the commit that switched prices to USD.",
    "",
    "ok",
])
def test_everything_else_is_work(text):
    assert classify(text) == WORK


def test_the_suite_tasks_are_classified_as_the_owner_would():
    wrong = []
    for task in load_tasks(TASKS):
        expected = QUESTION if task.category == "explain" or task.id in GIT_QUESTIONS else WORK
        if classify(task.prompt) != expected:
            wrong.append((task.id, expected))
    assert not wrong, wrong


def test_a_question_only_gets_the_reading_tools(tmp_path):
    agent, client, events = make_agent(tmp_path, [ModelReply(content="It adds two numbers.")])
    seen = {}
    real = client.complete
    client.complete = lambda messages, tools, on_text, on_reasoning: (
        seen.update(tools={t["function"]["name"] for t in tools}), real(messages, tools, on_text, on_reasoning))[1]
    agent.run("What does add() do?")
    assert seen["tools"] == READ_ONLY_TOOLS & {"read_file", "list_files", "search_code", "git"}
    assert "write_file" not in seen["tools"] and "edit_file" not in seen["tools"] and "run_shell" not in seen["tools"]
    assert "reads as a question" in client.seen[0][-1]["content"]


def test_work_gets_every_tool(tmp_path):
    agent, client, _ = make_agent(tmp_path, [ModelReply(content="Done.")])
    seen = {}
    real = client.complete
    client.complete = lambda messages, tools, on_text, on_reasoning: (
        seen.update(tools={t["function"]["name"] for t in tools}), real(messages, tools, on_text, on_reasoning))[1]
    agent.run("Fix the bug in hello.txt")
    assert {"write_file", "edit_file", "run_shell"} <= seen["tools"]
    assert "reads as a question" not in client.seen[0][-1]["content"]


def test_a_call_to_a_tool_a_question_doesnt_have_changes_nothing(tmp_path):
    agent, client, _ = make_agent(tmp_path, [
        call("write_file", '{"path": "hello.txt", "content": "changed"}'),
        ModelReply(content="I can't change files for a question."),
    ])
    agent.run("What would it take to rewrite hello.txt?")
    assert (tmp_path / "hello.txt").read_text() == "hi there\n"
    tool_message = [m for m in agent.messages if m["role"] == "tool"][0]
    assert "Not available" in tool_message["content"] and "/do" in tool_message["content"]


def test_do_runs_a_question_with_every_tool(tmp_path):
    agent, client, events = make_agent(tmp_path, [
        call("write_file", '{"path": "hello.txt", "content": "changed\\n"}'),
        ModelReply(content="Rewritten."),
    ])
    agent.run("What would it take to rewrite hello.txt?", intent=WORK)
    assert (tmp_path / "hello.txt").read_text() == "changed\n"


def test_the_turn_records_its_mode(tmp_path):
    from beastron.events import TurnStart

    agent, _, events = make_agent(tmp_path, [ModelReply(content="x"), ModelReply(content="y")])
    agent.run("What does add() do?")
    agent.run("Add a docstring to add().")
    assert [e.mode for e in events if isinstance(e, TurnStart)] == [QUESTION, WORK]


def test_the_known_small_model_slip_is_blocked_now(tmp_path, monkeypatch):
    """Editing when asked a question used to fail question-must-not-edit; the edit is not available any more."""
    import beastron.evals.runner as runner
    from scripted import EDIT_INSTEAD_OF_ANSWERING, PROFILE, factory

    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    _, [result] = runner.run_suite(
        load_tasks(TASKS, ids=["question-must-not-edit"]), [PROFILE], repeats=1,
        use_sandbox=False, client_factory=factory({"What would it take": EDIT_INSTEAD_OF_ANSWERING}),
    )
    assert result.changes == [] and result.checks[0]["type"] == "no_changes" and result.checks[0]["passed"]
