from beastron.observability import Observer
from beastron.events import StepObservation, ToolObservation, NumericTestClaims
from beastron.client import ModelReply
from test_agent import make_agent, call


def test_reads_counts_and_invalidation():
    ob=Observer()
    assert ob.tool_result('read_file', {'path':'a'}, '', 'lines 10-20 of 100', True)==(True,False)
    assert ob.tool_result('read_file', {'path':'a'}, '', 'lines 19-60 of 100', True)==(False,True)
    ob.tool_result('edit_file', {}, '', 'edited', True)
    assert ob.tool_result('read_file', {'path':'a'}, '', 'lines 10-20 of 100', True)==(True,False)


def test_numeric_claim_requires_literal_test_count_evidence():
    ob=Observer()
    ob.tool_result('run_shell', {}, '3 passed in 1.2s', 'exit 0', True)
    assert ob.unsupported_test_counts('3 tests passed.')==0
    assert ob.unsupported_test_counts('7 tests passed.')==1
    assert ob.unsupported_test_counts('Updated 7 files.')==0


def test_agent_emits_passive_measurements(tmp_path):
    agent,_,events=make_agent(tmp_path,[call('read_file','{"path":"hello.txt"}'),ModelReply(content='7 tests passed.',prompt_tokens=11,completion_tokens=12,reasoning_in_reply_chars=4)])
    assert agent.run('fix it')=='done'
    step=[e for e in events if isinstance(e,StepObservation)]
    assert step[-1].prompt_tokens==11 and step[-1].completion_tokens==12 and step[-1].reasoning_in_reply_chars==4
    tools=[e for e in events if isinstance(e,ToolObservation)]
    assert len(tools)==1 and tools[0].short_read and tools[0].elapsed>=0
    assert next(e for e in events if isinstance(e,NumericTestClaims)).unsupported_count==1
