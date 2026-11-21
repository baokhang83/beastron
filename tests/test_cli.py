"""The command line: `beastron` keeps working as before, and `beastron eval …` exists."""

import pytest
from typer.testing import CliRunner

import beastron.cli as cli
from beastron.agent import Agent


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "models.toml"
    path.write_text('[models.local]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\n'
                    '[models.other]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "o"\n')
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "state"))
    return path


@pytest.fixture
def calls(monkeypatch):
    """Record what beastron would do, without a model or a terminal."""
    seen = {}

    def fake_run(self, task):
        seen["task"], seen["model"] = task, self.client.profile.name
        seen["briefing"] = self.use_briefing
        return "done"

    monkeypatch.setattr(Agent, "run", fake_run)
    monkeypatch.setattr(cli, "_repl", lambda agent, ui, cfg, trace=None: seen.setdefault("repl", agent.client.profile.name))
    return seen


def invoke(*args):
    return CliRunner().invoke(cli.app, list(args))


def test_no_arguments_starts_the_interactive_session(config, calls):
    assert invoke("--config", str(config)).exit_code == 0  # options only: still the default command
    assert calls["repl"] == "local"


def test_a_task_runs_once(config, calls):
    result = invoke("--config", str(config), "fix the failing test")
    assert result.exit_code == 0
    assert calls["task"] == "fix the failing test" and "repl" not in calls


def test_options_before_the_task_keep_working(config, calls):
    result = invoke("-m", "other", "--no-briefing", "--no-trace", "--config", str(config), "a task")
    assert result.exit_code == 0
    assert (calls["task"], calls["model"], calls["briefing"]) == ("a task", "other", False)


def test_explicit_chat_command_and_a_task_named_like_a_subcommand(config, calls):
    assert invoke("chat", "--config", str(config), "eval").exit_code == 0
    assert calls["task"] == "eval"


def test_help_lists_the_commands():
    result = invoke("--help")
    assert result.exit_code == 0
    assert "chat" in result.output and "eval" in result.output


def test_eval_lists_its_subcommands():
    result = invoke("eval", "--help")
    assert result.exit_code == 0
    for name in ("run", "report", "baseline"):
        assert name in result.output


def test_eval_report_without_any_run_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("BEASTRON_STATE_DIR", str(tmp_path / "empty"))
    result = invoke("eval", "report")
    assert result.exit_code == 1
    assert "no eval runs" in result.output


def test_step_limit_follows_profile_unless_cli_overrides(config, monkeypatch):
    config.write_text('[models.local]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\nmax_steps = 40\n')
    seen = []
    def fake_run(self, task):
        seen.append((self.max_steps, self.client.profile.max_steps))
        return 'done'
    monkeypatch.setattr(Agent, 'run', fake_run)
    assert invoke('--config', str(config), '--no-trace', 'task').exit_code == 0
    assert invoke('--config', str(config), '--no-trace', '--max-steps', '25', 'task').exit_code == 0
    assert seen == [(None, 40), (25, 40)]
