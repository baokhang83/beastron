from io import StringIO

import pytest
from rich.console import Console

from beastron.events import ApprovalRequest, Preview
from beastron.ui import RichUI


def request(tool):
    return ApprovalRequest(tool, {}, Preview('command' if tool == 'run_shell' else 'diff', 'Preview', '-old\n+new'))


@pytest.mark.parametrize('tool', ['write_file', 'edit_file', 'delete_file'])
def test_auto_mode_shows_preview_without_prompting_for_file_tools(tool):
    output = StringIO()
    ui = RichUI(console=Console(file=output, force_terminal=False), mode='auto')
    assert ui.approve(request(tool))
    text = output.getvalue()
    assert '-old' in text and '+new' in text and 'Allow?' not in text


def test_auto_mode_still_prompts_for_shell(monkeypatch):
    ui = RichUI(console=Console(file=StringIO()), mode='auto')
    calls = []
    monkeypatch.setattr(ui.console, 'input', lambda prompt: calls.append(prompt) or 'n')
    assert not ui.approve(request('run_shell'))
    assert calls


@pytest.mark.parametrize('tool', ['write_file', 'edit_file', 'delete_file', 'run_shell'])
def test_ask_mode_prompts_for_mutations_and_shell(tool, monkeypatch):
    ui = RichUI(console=Console(file=StringIO()), mode='ask')
    calls = []
    monkeypatch.setattr(ui.console, 'input', lambda prompt: calls.append(prompt) or 'n')
    assert not ui.approve(request(tool)) and calls


def test_auto_mode_keeps_workspace_and_git_write_protections(tmp_path, monkeypatch):
    from beastron.agent import Agent
    from beastron.client import ModelReply
    from beastron.tools import Workspace, make_tools
    from test_agent import FakeClient, call, tool_results

    project = tmp_path / 'project'
    project.mkdir()
    (project / '.git').mkdir()
    outside = tmp_path / 'outside.txt'
    replies = [call('write_file', '{"path":"../outside.txt","content":"bad"}'),
               call('write_file', '{"path":".git/config","content":"bad"}'),
               call('write_file', '{"path":"inside.txt","content":"ok"}'), ModelReply(content='done')]
    ui = RichUI(console=Console(file=StringIO()), mode='auto')
    monkeypatch.setattr(ui.console, 'input', lambda prompt: pytest.fail('unexpected approval prompt'))
    ws = Workspace(project)
    agent = Agent(FakeClient(replies), ws, make_tools(ws), lambda e: None, ui.approve, briefing=False)
    assert agent.run('make files') == 'done'
    assert not outside.exists() and not (project / '.git/config').exists()
    assert (project / 'inside.txt').read_text() == 'ok'
    assert 'outside the project' in tool_results(agent)[0] and '.git' in tool_results(agent)[1]


def test_auto_mode_allows_simple_project_file_search(monkeypatch):
    ui = RichUI(console=Console(file=StringIO()), mode='auto')
    monkeypatch.setattr(ui.console, 'input', lambda prompt: pytest.fail('search should not prompt'))
    command = 'find . -name "stageB-handoff.md" -type f'
    assert ui.approve(ApprovalRequest('run_shell', {'command': command}, Preview('command', 'search', command)))


@pytest.mark.parametrize('command', [
    'find . -name x -type f -delete', 'find . -name x -exec rm {} \\;',
    'find / -name x -type f', 'find . -name x -type f; rm -rf .',
    'find . -name "$(touch hacked)" -type f', 'find . -name x -type f > out',
    'find . -name `touch_x` -type f', 'find . -name x -type f | xargs rm',
    'find . -name x -type f\nrm -rf .', 'find .\n-name x -type f',
])
def test_auto_mode_still_prompts_for_other_shell_commands(command, monkeypatch):
    ui = RichUI(console=Console(file=StringIO()), mode='auto')
    monkeypatch.setattr(ui.console, 'input', lambda prompt: 'n')
    assert not ui.approve(ApprovalRequest('run_shell', {'command': command}, Preview('command', 'shell', command)))


def test_ask_mode_prompts_for_simple_file_search(monkeypatch):
    ui = RichUI(console=Console(file=StringIO()), mode='ask')
    monkeypatch.setattr(ui.console, 'input', lambda prompt: 'n')
    command = 'find . -name "stageB-handoff.md" -type f'
    assert not ui.approve(ApprovalRequest('run_shell', {'command': command}, Preview('command', 'search', command)))


def test_auto_mode_searches_and_deletes_with_no_approval_prompt(tmp_path, monkeypatch):
    from beastron.agent import Agent
    from beastron.client import ModelReply
    from beastron.tools import Workspace, make_tools
    from test_agent import FakeClient, call, tool_results
    (tmp_path / 'stageB-handoff.md').write_text('temporary')
    ui = RichUI(console=Console(file=StringIO()), mode='auto')
    monkeypatch.setattr(ui.console, 'input', lambda prompt: pytest.fail('unexpected approval prompt'))
    replies = [call('run_shell', '{"command":"find . -name \\"stageB-handoff.md\\" -type f"}'),
               call('delete_file', '{"path":"stageB-handoff.md"}'), ModelReply(content='Done.')]
    ws = Workspace(tmp_path)
    agent = Agent(FakeClient(replies), ws, make_tools(ws), lambda e: None, ui.approve, briefing=False)
    assert agent.run('delete the file') == 'done'
    assert 'stageB-handoff.md' in tool_results(agent)[0]
    assert not (tmp_path / 'stageB-handoff.md').exists()


def test_auto_delete_shows_at_most_three_diff_lines_and_no_approval_message():
    output = StringIO()
    ui = RichUI(console=Console(file=output, force_terminal=False, width=120), mode='auto')
    body = '\n'.join(f'-line-{i}' for i in range(8))
    assert ui.approve(ApprovalRequest('delete_file', {'path': 'a'}, Preview('diff', 'delete a', body)))
    text = output.getvalue()
    assert all(f'-line-{i}' in text for i in range(3))
    assert all(f'-line-{i}' not in text for i in range(3, 8))
    assert '5 more lines' in text
    assert 'approved automatically' not in text


def test_auto_file_approval_is_silent_apart_from_preview():
    output = StringIO()
    ui = RichUI(console=Console(file=output, force_terminal=False), mode='auto')
    assert ui.approve(request('write_file'))
    assert 'approved automatically' not in output.getvalue()


def test_delete_preview_shows_three_content_lines_without_diff_headers():
    output = StringIO()
    ui = RichUI(console=Console(file=output, force_terminal=False, width=80), mode='auto')
    body = '--- a/file\n+++ b/file\n@@ -1,5 +0,0 @@\n-one\n-two\n-three\n-four\n-five\n'
    assert ui.approve(ApprovalRequest('delete_file', {}, Preview('diff', 'delete file', body)))
    text = output.getvalue()
    assert all(line in text for line in ('-one', '-two', '-three'))
    assert '-four' not in text and '2 more lines' in text
