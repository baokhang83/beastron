import json
import subprocess

import pytest
from beastron.agent import Agent
from beastron.discard_guard import DiscardGuard, attempts
from beastron.events import DiscardAttempt
from beastron.client import ModelReply
from beastron.quirks import ToolCall
from beastron.tools import Workspace, make_tools
from test_agent import FakeClient


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    (tmp_path / 'a.txt').write_text('original\n')
    (tmp_path / 'b.txt').write_text('original\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'initial')
    return tmp_path


@pytest.mark.parametrize('command', ['git stash', 'git stash push', 'git checkout -- a.txt', 'git checkout -f', 'git reset --hard', 'git restore a.txt', 'git clean -fd'])
def test_allows_if_agent_made_no_changes(repo, command):
    (repo / 'a.txt').write_text('existing user changes\n')
    guard = DiscardGuard(repo)
    outcomes = guard.check(command)
    assert outcomes and not outcomes[0][1]


@pytest.mark.parametrize('command', ['git stash', 'git stash push', 'git checkout -- a.txt', 'git checkout -f', 'git reset --hard', 'git restore a.txt'])
def test_blocks_tracked_agent_change(repo, command):
    guard = DiscardGuard(repo)
    (repo / 'a.txt').write_text('agent change\n')
    assert guard.check(command)[0][1]


def test_paths_do_not_block_unrelated_file(repo):
    guard = DiscardGuard(repo)
    (repo / 'a.txt').write_text('agent change\n')
    assert not guard.check('git restore b.txt')[0][1]
    assert guard.check('git restore a.txt')[0][1]


def test_clean_and_stash_untracked_handling(repo):
    guard = DiscardGuard(repo)
    (repo / 'new.txt').write_text('agent change\n')
    assert guard.check('git clean -fd')[0][1]
    assert not guard.check('git clean -nd')[0][1]
    assert not guard.check('git stash')[0][1]
    assert guard.check('git stash -u')[0][1]


def test_agent_logs_allowed_and_blocked_and_keeps_edit(repo):
    def call(i, name, **args):
        return ModelReply(tool_calls=[ToolCall(str(i), name, json.dumps(args))])
    replies = [call(1, 'run_shell', command='git stash'),
               call(2, 'edit_file', path='a.txt', old_text='original', new_text='agent change'),
               call(3, 'run_shell', command='git stash'), ModelReply(content='done')]
    ws=Workspace(repo); events=[]
    agent=Agent(FakeClient(replies), ws, make_tools(ws), events.append, lambda _: True, briefing=False)
    assert agent.run('fix it') == 'done'
    assert (repo/'a.txt').read_text() == 'agent change\n'
    assert [e.blocked for e in events if isinstance(e, DiscardAttempt)] == [False, True]
    assert any('read-only git tool' in m.get('content', '') for m in agent.messages if m['role']=='tool')


def test_read_only_and_benign_git_not_recognized():
    assert not attempts('git stash list')
    assert not attempts('git checkout -b feature')
    assert not attempts('echo git stash')


def test_restore_staged_does_not_discard_unstaged_edit(repo):
    guard = DiscardGuard(repo)
    (repo/'a.txt').write_text('agent change\n')
    assert not guard.check('git restore --staged a.txt')[0][1]
    git(repo, 'add', 'a.txt')
    assert guard.check('git restore --staged a.txt')[0][1]
