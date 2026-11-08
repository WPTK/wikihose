"""Small widgets and helpers shared by the tabs and the shell."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

BARS = "▁▂▃▄▅▆▇█"


def spark(series: tuple[int, ...], width: int) -> str:
    """A sparkline of ``width`` cells for the daily series; days are merged (the largest wins) when they do not fit."""
    if not series or width <= 0:
        return ""
    count = min(width, len(series))
    cells = []
    for i in range(count):
        start = i * len(series) // count
        end = max((i + 1) * len(series) // count, start + 1)
        cells.append(max(series[start:end]))
    top = max(cells) or 1
    return "".join(BARS[min(len(BARS) - 1, c * len(BARS) // (top + 1))] for c in cells)


class PageTable(DataTable[Any]):
    """A table whose rows are Wikipedia pages. The shell asks it for the page under the cursor (the ``w`` key, unless
    ``watchable`` is False, as on the Watchlist tab, where ``w`` types a new page).

    Enter, and a double click on a row, post ``Open`` and the shell opens that page in the web browser. A single click
    only moves the cursor.
    """

    class Open(Message):
        """The reader asked to open the page under the cursor on its wiki."""

        def __init__(self, table: PageTable) -> None:
            super().__init__()
            self.table = table

        @property
        def control(self) -> PageTable:
            return self.table

    # Two bindings for the one key, as the app has for space: exactly one is enabled (see check_action). Only the first
    # shows in the footer, so a tab whose footer is already full can drop the label and keep the key.
    BINDINGS = [
        Binding(
            "enter",
            "open_page",
            "Open",
            key_display="enter",  # Textual draws the key as a return glyph; every other key in the footer is a word
            tooltip="Open the selected page on Wikipedia",
        ),
        Binding(
            "enter",
            "open_page_quiet",
            "Open",
            key_display="enter",
            show=False,
            tooltip="Open the selected page on Wikipedia",
        ),
    ]

    show_open_in_footer: bool = True

    def __init__(
        self, resolve: Callable[[str], tuple[str, str] | None], *, watchable: bool = True, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._resolve = resolve
        self.watchable = watchable  # whether ``w`` watches the page under the cursor (not on the list of watched pages)

    def page_at_cursor(self) -> tuple[str, str] | None:
        """``(wiki, title)`` of the row under the cursor, or None when the table is empty."""
        key = self.cursor_key()
        return None if key is None else self._resolve(key)

    def cursor_key(self) -> str | None:
        if not self.row_count:
            return None
        return self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "open_page":
            return self.show_open_in_footer
        if action == "open_page_quiet":
            return not self.show_open_in_footer
        return super().check_action(action, parameters)

    def set_open_in_footer(self, show: bool) -> None:
        """Show the ``enter Open`` label in the footer or leave it out (the key works either way)."""
        if show != self.show_open_in_footer:
            self.show_open_in_footer = show
            self.refresh_bindings()

    def action_open_page(self) -> None:
        self._open()

    def action_open_page_quiet(self) -> None:
        self._open()

    def _open(self) -> None:
        if self.page_at_cursor() is not None:
            self.post_message(self.Open(self))

    async def _on_click(self, event: events.Click) -> None:
        await super()._on_click(event)  # moves the cursor to the clicked row
        if event.chain == 2 and event.style.meta.get("row", -1) >= 0 and event.style.meta.get("column", -1) >= 0:
            self._open()
