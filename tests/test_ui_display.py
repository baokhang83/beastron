from io import StringIO
from rich.console import Console
from beastron.events import ModelStart, ModelEnd, TextDelta
from beastron.ui import RichUI, visible_model_text


def test_markup_is_hidden_as_stream_arrives():
    content = "I'll check.\n\n<function=git>\n<parameter=command>status</parameter>\n</function>\n</tool_call>"
    for i in range(len(content) + 1):
        text = visible_model_text(content[:i])
        assert '<function' not in text and '<parameter' not in text and '</tool_call>' not in text
    assert visible_model_text(content).strip() == "I'll check."


def test_normal_text_is_preserved():
    assert visible_model_text('The answer is 2 < 3.') == 'The answer is 2 < 3.'


def test_raw_markup_not_printed_in_final_display():
    stream = StringIO()
    ui = RichUI(console=Console(file=stream, force_terminal=False))
    ui.handle(ModelStart(1))
    ui.handle(TextDelta('Checking.\n\n<function=git>\n<parameter=command>status</parameter>\n</function>\n</tool_call>'))
    ui.handle(ModelEnd(1))
    assert 'Checking.' not in stream.getvalue()
    assert '<function' not in stream.getvalue() and '<parameter' not in stream.getvalue()


def test_markup_in_explanatory_code_block_is_preserved():
    content = 'The syntax is:\n```xml\n<function=git>\n</function>\n</tool_call>\n```'
    assert visible_model_text(content) == content


def test_final_text_ending_with_angle_bracket_is_not_lost():
    assert visible_model_text('Type <', final=True) == 'Type <'


def test_non_repository_git_error_is_concise_but_verbose_shows_detail():
    from beastron.events import ToolEnd
    detail = 'Error: This project is not a git repository, so there is no history to look at.'
    for verbose in (False, True):
        stream = StringIO()
        ui = RichUI(console=Console(file=stream, force_terminal=False), verbose=verbose)
        ui.handle(ToolEnd('c', 'git', False, detail.removeprefix('Error: '), detail))
        text = stream.getvalue()
        assert 'not a Git repository' in text
        assert ('so there is no history to look at' in text) is verbose


def test_other_git_errors_keep_their_detail():
    from beastron.events import ToolEnd
    stream = StringIO()
    ui = RichUI(console=Console(file=stream, force_terminal=False))
    ui.handle(ToolEnd('c', 'git', False, 'invalid ref', 'Error: invalid ref'))
    assert 'invalid ref' in stream.getvalue()


def test_malformed_tool_reply_does_not_leave_repeated_prose():
    stream = StringIO()
    ui = RichUI(console=Console(file=stream, force_terminal=False))
    ui.handle(ModelStart(1))
    ui.handle(TextDelta("I'll check.\n<function=git></function></tool_call>"))
    ui.handle(ModelEnd(1))
    assert "I'll check" not in stream.getvalue()


def test_empty_change_record_is_not_printed():
    from beastron.events import TurnEnd
    stream = StringIO()
    ui = RichUI(console=Console(file=stream, force_terminal=False))
    ui.handle(TurnEnd('incomplete', 3, 0, 0, 1, 1))
    assert 'incomplete' in stream.getvalue().lower()
    assert 'no files changed' not in stream.getvalue()


def test_model_text_is_not_streamed_before_tool_markup_can_be_detected(monkeypatch):
    class LiveProbe:
        def __init__(self):
            self.updates = []
        def update(self, item):
            self.updates.append(item)
        def stop(self):
            pass
    live = LiveProbe()
    stream = StringIO()
    ui = RichUI(console=Console(file=stream, force_terminal=False))
    monkeypatch.setattr(ui, '_start_live', lambda item: setattr(ui, '_live', live))
    ui.handle(ModelStart(1))
    ui.handle(TextDelta("I'll check it.\n"))
    ui.handle(TextDelta('<function=git></function></tool_call>'))
    ui.handle(ModelEnd(1))
    assert not live.updates
    assert "I'll check it" not in stream.getvalue()
