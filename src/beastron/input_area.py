"""The input area: a grey band across the terminal where you type.

    ┌──────────────────────────────────────────────┐  grey band, full width
    │                                              │  padding line
    │ › fix the failing test in utils              │  your text
    │   and run the tests afterwards               │  continuation line
    │                                              │  padding line
    └──────────────────────────────────────────────┘
      [auto]                                        permission mode

Enter sends. Option+Enter (Esc then Enter) or Ctrl-J adds a new line. Pasted
text keeps its line breaks instead of being sent line by line. After you send,
the message is printed again as the same grey band, so the conversation shows
what you asked above each answer.

Colors: dark by default. BEASTRON_THEME=light switches to a light band; terminals
that report a light background through COLORFGBG get it automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer
from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import is_done, to_filter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, HSplit, VerticalAlign, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

PLACEHOLDER = "Ask beastron to do anything · /help for commands"
MARK = " › "


@dataclass(frozen=True)
class Theme:
    band: str  # background of the input area
    text: str
    mark: str  # the › prompt mark
    placeholder: str


DARK = Theme(band="#505050", text="#ffffff", mark="#5fd7ff", placeholder="#a0a0a0")
LIGHT = Theme(band="#ebebeb", text="#1c1c1c", mark="#0087af", placeholder="#8a8a8a")


def pick_theme() -> Theme:
    choice = os.environ.get("BEASTRON_THEME", "").strip().lower()
    if choice in ("dark", "light"):
        return DARK if choice == "dark" else LIGHT
    # Some terminals set COLORFGBG="foreground;background" (ANSI color numbers).
    background = os.environ.get("COLORFGBG", "").rsplit(";", 1)[-1]
    if background.isdigit() and int(background) in (7, 15):  # white / bright white
        return LIGHT
    return DARK


class InputArea:
    def __init__(self, history_file: Path, completer: Completer, theme: Theme | None = None,
                 indicator=None, mode_bindings: KeyBindings | None = None) -> None:
        self.theme = theme or pick_theme()
        t = self.theme
        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file)),
            completer=completer,
            complete_while_typing=True,
            multiline=True,
            key_bindings=merge_key_bindings([_key_bindings(), mode_bindings]) if mode_bindings else _key_bindings(),
            style=Style.from_dict(
                {
                    # The band color is set on the container of the input lines (see
                    # _paint_band), not here: this base style also covers the empty rows
                    # below the prompt, which must keep the terminal's own background.
                    "": t.text,
                    "mark": f"bg:{t.band} {t.mark} bold",
                    "placeholder": f"bg:{t.band} {t.placeholder}",
                    "band": f"bg:{t.band}",
                }
            ),
            include_default_pygments_style=False,
            placeholder=FormattedText([("class:placeholder", PLACEHOLDER)]),
            prompt_continuation=lambda width, line_number, is_soft_wrap: [("class:mark", " " * width)],
            # The live prompt is erased on Enter and printed again by echo().
            erase_when_done=True,
            reserve_space_for_menu=0,
        )
        _paint_band(self.session)
        layout = self.session.app.layout.container
        layout.align = VerticalAlign.TOP
        layout.children.insert(1, ConditionalContainer(Window(height=1, style="class:band"), filter=~is_done))
        if indicator is not None:
            layout.children.insert(2, ConditionalContainer(
                Window(FormattedTextControl(indicator), height=1, dont_extend_height=True), filter=~is_done
            ))

    def read(self) -> str:
        """Show the input area and return what was typed. Raises EOFError on Ctrl-D."""
        return self.session.prompt(self._above)

    def echo(self, console: Console, text: str) -> None:
        """Print a sent message as the same grey band, above the answer."""
        t = self.theme
        # Two columns, so long lines wrap under the text instead of under the mark.
        grid = Table.grid(expand=True)
        grid.add_column(width=len(MARK), no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text(MARK, style=f"bold {t.mark}"), Text(text, style=t.text))
        console.print(Padding(grid, (1, 0), style=f"on {t.band}", expand=True))

    def _above(self) -> list[tuple[str, str]]:
        # A padding line, then the mark. Lines of the prompt message above the
        # last one are drawn in their own row, filled with the band color.
        return [("class:band", " \n"), ("class:mark", MARK)]



def _paint_band(session: PromptSession[str]) -> None:
    """Give the input lines a background across the full width.

    A style on the text only colors the characters you type. A style on the
    container that holds the prompt and input lines colors every cell of those
    rows. prompt_toolkit doesn't expose that container, so find it in the layout:
    it's the one whose children include the input window.
    """
    input_window = next(
        w for w in session.layout.find_all_windows()
        if isinstance(w.content, BufferControl) and w.content.buffer.name == DEFAULT_BUFFER
    )
    # prompt_toolkit lets the input window grow into the empty rows below it.
    # Those rows are normally invisible; with a band color they would turn grey
    # down to the bottom of the screen. Keep the window as tall as its text.
    input_window.dont_extend_height = to_filter(True)
    for container in session.layout.walk():
        if isinstance(container, HSplit) and any(
            child is input_window or getattr(child, "content", None) is input_window
            for child in container.children
        ):
            container.style = "class:band"
            return
    input_window.style = "class:band"  # fallback: at least the input lines


def _key_bindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter")
    def _send(event) -> None:
        buffer = event.current_buffer
        state = buffer.complete_state
        if state and state.current_completion:  # Enter picks the highlighted /command
            buffer.apply_completion(state.current_completion)
            return
        buffer.validate_and_handle()

    @kb.add("escape", "enter")  # Option+Enter, with "Use Option as Meta key" on
    @kb.add("c-j")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    return kb
