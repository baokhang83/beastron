from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from beastron.cli import SlashCompleter
from beastron.input_area import DARK, LIGHT, InputArea, pick_theme

ENTER = "\r"
CTRL_J = "\n"
OPTION_ENTER = "\x1b\r"  # Esc then Enter, what Option+Enter sends with "Use Option as Meta key"


def type_into(tmp_path, keys):
    """Feed keystrokes to a fresh input area, as a terminal would, and return what it gives back."""
    with create_pipe_input() as inp:
        inp.send_text(keys)
        with create_app_session(input=inp, output=DummyOutput()):
            area = InputArea(tmp_path / "history", SlashCompleter(), theme=DARK)
            return area.read()


def test_enter_sends(tmp_path):
    assert type_into(tmp_path, "fix the test" + ENTER) == "fix the test"


def test_ctrl_j_and_option_enter_add_a_new_line(tmp_path):
    assert type_into(tmp_path, "one" + CTRL_J + "two" + OPTION_ENTER + "three" + ENTER) == "one\ntwo\nthree"


def test_pasted_text_keeps_its_line_breaks(tmp_path):
    paste = "\x1b[200~first line\nsecond line\x1b[201~"  # bracketed paste, as terminals send it
    assert type_into(tmp_path, paste + ENTER) == "first line\nsecond line"


def test_band_is_painted_on_the_input_container(tmp_path):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        area = InputArea(tmp_path / "history", SlashCompleter(), theme=DARK)
    # the layout walk can reach the same container by two paths, so count distinct ones
    banded = {id(c) for c in area.session.layout.walk() if isinstance(c, HSplit) and c.style == "class:band"}
    assert len(banded) == 1


def test_echo_keeps_wrapped_lines_under_the_text(tmp_path):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        area = InputArea(tmp_path / "history", SlashCompleter(), theme=DARK)
    console = Console(record=True, width=30, color_system=None)
    area.echo(console, "a request that is too long for one line")
    lines = console.export_text().splitlines()
    assert lines[0].strip() == ""  # top padding
    assert lines[1].startswith(" › a request")
    assert lines[2].startswith("   ") and lines[2].strip()  # wrapped, indented under the text
    assert lines[-1].strip() == ""  # bottom padding


def test_theme_choice(monkeypatch):
    monkeypatch.delenv("BEASTRON_THEME", raising=False)
    monkeypatch.delenv("COLORFGBG", raising=False)
    assert pick_theme() is DARK
    monkeypatch.setenv("COLORFGBG", "0;15")  # dark text on a white background
    assert pick_theme() is LIGHT
    monkeypatch.setenv("BEASTRON_THEME", "dark")  # an explicit choice wins
    assert pick_theme() is DARK


def test_shift_tab_toggles_mode_without_affecting_input(tmp_path):
    from io import StringIO
    from beastron.cli import _mode_bindings, _mode_prompt
    from beastron.ui import RichUI
    ui = RichUI(console=Console(file=StringIO()), mode='auto')
    with create_pipe_input() as inp:
        inp.send_text('\x1b[Zhello\r')
        with create_app_session(input=inp, output=DummyOutput()):
            area = InputArea(tmp_path / 'history', SlashCompleter(), theme=DARK,
                             indicator=lambda: _mode_prompt(ui), mode_bindings=_mode_bindings(ui))
            assert area.read() == 'hello'
    assert ui.mode == 'ask'
