import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from beastron.client import OpenAICompatClient
from beastron.quirks import recover_function_markup
from beastron.tools import Workspace, make_tools
from test_agent import PROFILE


@pytest.fixture
def schemas(tmp_path):
    return [tool.schema() for tool in make_tools(Workspace(tmp_path))]


def test_recovers_missing_opening_tool_call_with_arguments(schemas):
    text = 'Checking.\n<function=delete_file>\n<parameter=path>\ntest.md\n</parameter>\n</function>\n</tool_call>'
    prefix, call = recover_function_markup(text, schemas)
    assert prefix == 'Checking.' and call.name == 'delete_file'
    assert json.loads(call.arguments) == {'path': 'test.md'}


def test_recovers_no_argument_function(schemas):
    prefix, call = recover_function_markup('<function=list_files>\n</function>\n</tool_call>', schemas)
    assert prefix == '' and call.name == 'list_files' and json.loads(call.arguments) == {}


@pytest.mark.parametrize('text', [
    '<function=delete_file><parameter=path>x</parameter></function>',
    '<function=unknown></function></tool_call>',
    '<function=delete_file><parameter=path>x</parameter><parameter=path>y</parameter></function></tool_call>',
    '<function=delete_file><parameter=bad>x</parameter></function></tool_call>',
    '<function=delete_file></function></tool_call>',
    '<function=delete_file><parameter=path>x</parameter></function></tool_call>extra',
    '```\n<function=delete_file><parameter=path>x</parameter></function></tool_call>\n```',
    '<function=delete_file><parameter=path>x</parameter></function><function=list_files></function></tool_call>',
])
def test_rejects_ambiguous_or_unauthorized_tool_markup(text, schemas):
    assert recover_function_markup(text, schemas) is None


def client_reply(text, profile, schemas, tool_calls=None):
    client = OpenAICompatClient(profile)
    delta = SimpleNamespace(content=text, model_extra={}, tool_calls=tool_calls or [])
    chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: iter([chunk]))))
    return client.complete([], schemas, lambda _: None, lambda _: None)


def test_recovery_is_opt_in_and_normal_call_path(schemas):
    text = 'Checking.\n<function=delete_file><parameter=path>x</parameter></function></tool_call>'
    assert not client_reply(text, PROFILE, schemas).tool_calls
    profile = replace(PROFILE, quirks=frozenset({'recover_function_markup'}))
    reply = client_reply(text, profile, schemas)
    assert reply.content == 'Checking.' and reply.tool_calls[0].name == 'delete_file'
    assert json.loads(reply.tool_calls[0].arguments) == {'path': 'x'}


def test_recovers_function_inside_proper_tool_call_wrapper(schemas):
    text = '<tool_call><function=list_files></function></tool_call>'
    profile = replace(PROFILE, quirks=frozenset({'recover_function_markup'}))
    assert client_reply(text, profile, schemas).tool_calls[0].name == 'list_files'


def test_recovered_file_call_still_goes_through_approval(tmp_path):
    from beastron.agent import Agent
    from beastron.tools import Workspace, make_tools
    path = tmp_path / 'protected.txt'
    path.write_text('keep')
    profile = replace(PROFILE, quirks=frozenset({'recover_function_markup'}))
    client = OpenAICompatClient(profile)
    raw = '<function=delete_file>\n<parameter=path>\nprotected.txt\n</parameter>\n</function>\n</tool_call>'
    delta = SimpleNamespace(content=raw, model_extra={}, tool_calls=[])
    chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: iter([chunk]))))
    asked = []
    ws = Workspace(tmp_path)
    agent = Agent(client, ws, make_tools(ws), lambda event: None,
                  lambda request: asked.append(request.tool) or False, briefing=False)
    assert agent.run('delete protected.txt') == 'denied'
    assert asked == ['delete_file'] and path.read_text() == 'keep'


def test_recovery_respects_read_only_tool_schemas(tmp_path):
    from beastron.policy import READ_ONLY_TOOLS
    schemas = [t.schema() for t in make_tools(Workspace(tmp_path)) if t.name in READ_ONLY_TOOLS]
    text = '<function=delete_file><parameter=path>x</parameter></function></tool_call>'
    assert recover_function_markup(text, schemas) is None
