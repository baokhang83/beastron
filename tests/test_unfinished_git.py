"""End-of-turn reminders for actual Git operations, without model or GPU calls."""
import subprocess
import pytest
from beastron.agent import Agent
from beastron.client import ModelReply
from beastron.events import Nudge
from beastron.tools import Workspace, make_tools
from test_agent import FakeClient


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    (tmp_path/'a').write_text('initial\n')
    git(tmp_path, 'add', 'a')
    git(tmp_path, 'commit', '-qm', 'initial')
    return tmp_path


def agent_for(repo, replies):
    client=FakeClient(replies); events=[]; ws=Workspace(repo)
    return Agent(client,ws,make_tools(ws),events.append,lambda _:True,briefing=False),client,events


@pytest.mark.parametrize('marker,operation', [('MERGE_HEAD','merge'),('CHERRY_PICK_HEAD','cherry-pick'),('rebase-merge','rebase'),('rebase-apply','rebase')])
def test_unfinished_operation_gets_one_neutral_reminder(repo, marker, operation):
    gitdir=(repo/git(repo,'rev-parse','--git-dir')).resolve()
    if marker.startswith('rebase-'): (gitdir/marker).mkdir()
    else: (gitdir/marker).write_text(git(repo,'rev-parse','HEAD')+'\n')
    agent,client,events=agent_for(repo,[ModelReply(content='Finished.'),ModelReply(content='Still working.')])
    assert agent.run('fix it')=='done'
    assert len(client.seen)==2
    reminder=client.seen[1][-1]['content']
    assert operation in reminder.lower() and "If you can't, say so" in reminder
    assert len([e for e in events if isinstance(e,Nudge)])==1


def test_clean_git_repo_does_not_get_reminder(repo):
    agent,client,_=agent_for(repo,[ModelReply(content='Done.')])
    assert agent.run('fix it')=='done'
    assert len(client.seen)==1


def test_non_git_directory_does_not_get_reminder(tmp_path):
    agent,client,_=agent_for(tmp_path,[ModelReply(content='Done.')])
    assert agent.run('fix it')=='done'
    assert len(client.seen)==1


def test_worktree_uses_actual_git_dir(repo,tmp_path):
    branch=tmp_path/'linked';git(repo,'worktree','add','-qb','linked',str(branch))
    gitdir=(branch/git(branch,'rev-parse','--git-dir')).resolve()
    (gitdir/'MERGE_HEAD').write_text(git(branch,'rev-parse','HEAD')+'\n')
    agent,client,_=agent_for(branch,[ModelReply(content='Done.'),ModelReply(content='Cannot finish.')])
    assert agent.run('fix it')=='done'
    assert len(client.seen)==2
