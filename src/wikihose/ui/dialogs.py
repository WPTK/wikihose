"""The small dialogs: help, add a page, change the wiki, and the detail of one edit."""

from __future__ import annotations

from datetime import tzinfo

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.style import Style
from textual.widgets import Button, Input, Label, Static

from wikihose.config import is_valid_wiki
from wikihose.sources.events import Edit
from wikihose.titles import article_url

# Help lines that are not key bindings: the shell's tab keys and how focus and the mouse work.
FIXED_HELP: list[tuple[str, str]] = [
    ("1-5", "Switch tab"),
    ("Enter", "Open the selected row"),
    ("Tab", "Move between panes"),
    ("Mouse", "Click tabs, rows, footer keys and buttons"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark", "dismiss", "Close", show=False)]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Vertical { width: 72; height: auto; max-height: 100%; border: round $primary; background: $surface;
        padding: 0 1; }
    HelpScreen VerticalScroll { height: auto; }
    HelpScreen .help-line { width: 100%; }
    HelpScreen Button { margin-top: 1; }
    """

    def __init__(self, lines: list[tuple[str, str]]) -> None:
        super().__init__()
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Content.assemble(("Keys", "bold")))
            with VerticalScroll():
                for key, text in self.lines:
                    yield Label(Content.assemble("  ", (f"{key:<8}", "bold $accent"), text), classes="help-line")
            yield Button("Close", id="close")

    def on_button_pressed(self) -> None:
        self.dismiss()


class AddPage(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel", show=False)]
    DEFAULT_CSS = """
    AddPage { align: center middle; }
    AddPage > Vertical { width: 64; height: auto; border: round $primary; background: $surface;
        padding: 0 1; }
    AddPage Horizontal { height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Add a page to the watchlist")
            yield Input(placeholder="Article title or Wikipedia URL", id="page")
            with Horizontal():
                yield Button("Add", id="add", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        text = self.query_one(Input).value.strip()
        self.dismiss(text or None if event.button.id == "add" else None)


class WikiPrompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel", show=False)]
    DEFAULT_CSS = """
    WikiPrompt { align: center middle; }
    WikiPrompt > Vertical { width: 64; height: auto; border: round $primary; background: $surface;
        padding: 0 1; }
    WikiPrompt #wiki-error { color: $error; height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Watch another wiki")
            yield Input(placeholder="Wiki name, such as enwiki or dewiki", id="wiki")
            yield Static("", id="wiki-error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip().lower()
        if is_valid_wiki(name):
            self.dismiss(name)
        else:
            self.query_one("#wiki-error", Static).update("Use a wiki name like enwiki or dewiki.")


class EditDetail(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close", show=False)]
    DEFAULT_CSS = """
    EditDetail { align: center middle; }
    EditDetail > Vertical { width: 72; height: auto; max-height: 100%; border: round $primary; background: $surface;
        padding: 0 1; }
    EditDetail Label { width: 100%; }
    EditDetail Button { margin-top: 1; }
    """

    def __init__(self, wiki: str, title: str, edit: Edit | None, tz: tzinfo) -> None:
        super().__init__()
        self.wiki = wiki
        self.title_text = title
        self.edit = edit
        self.tz = tz

    @property
    def url(self) -> str | None:
        """The article link, or None when the event did not name a Wikimedia host."""
        return article_url(self.edit.server_name, self.title_text) if self.edit else None

    def compose(self) -> ComposeResult:
        edit = self.edit
        with Vertical():
            yield self._line("Title", self.title_text)
            yield self._line("Wiki", self.wiki)
            if edit is None:
                yield self._line("Edit", "No recent edit to show.")
            else:
                yield self._line("Editor", edit.user + (" (bot)" if edit.bot else ""))
                yield self._line("Time", edit.at.astimezone(self.tz).strftime("%a %d %b %Y %H:%M:%S %Z"))
                yield self._line("Bytes", f"{edit.delta:+,}" + ("  (new page)" if edit.kind == "new" else ""))
                yield self._line("Summary", edit.comment or "(none)")
            url = self.url
            if url is not None:
                link = Content.assemble((f"{'Link':<8}", "$text-muted"), (url, Style(link=url, underline=True)))
                yield Label(link, id="link")
            yield Button("Close", id="close")

    @staticmethod
    def _line(name: str, value: str) -> Label:
        return Label(Content.assemble((f"{name:<8}", "$text-muted"), value))

    def on_button_pressed(self) -> None:
        self.dismiss()
