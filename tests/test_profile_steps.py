from dataclasses import replace

import pytest

from beastron.config import ConfigError, load_config
from beastron.client import ModelReply
from beastron.evals.runner import run_task
from test_agent import PROFILE, FakeClient, call
from test_eval_runner import make_task


def write_config(tmp_path, extra=''):
    path = tmp_path / 'models.toml'
    path.write_text('[models.test]\nbase_url = "http://unused"\nmodel = "fake"\n' + extra)
    return path


def test_profile_step_limit_default_and_configured(tmp_path):
    assert load_config(write_config(tmp_path)).get(None).max_steps == 25
    assert load_config(write_config(tmp_path, 'max_steps = 40\n')).get(None).max_steps == 40


@pytest.mark.parametrize('value', ['0', '-1', 'true', '1.5', '"40"'])
def test_profile_step_limit_must_be_positive_integer(tmp_path, value):
    with pytest.raises(ConfigError, match='max_steps'):
        load_config(write_config(tmp_path, f'max_steps = {value}\n'))


@pytest.mark.parametrize(('task_extra', 'expected'), [('', 3), ('max_steps = 2\n', 2), ('max_steps = 25\n', 25)])
def test_eval_uses_profile_limit_unless_task_specifies_one(tmp_path, task_extra, expected):
    task = make_task(tmp_path, toml_extra=task_extra)
    profile = replace(PROFILE, max_steps=3)
    run_dir = tmp_path / 'run'
    (run_dir / 'traces').mkdir(parents=True)
    def factory(p):
        client = FakeClient([call('list_files', '{}') for _ in range(26)])
        client.profile = p
        return client
    result = run_task(task, profile, run_dir=run_dir, use_sandbox=False, client_factory=factory)
    assert result.reason == 'max_steps' and result.steps == expected
