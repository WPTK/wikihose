"""Small widgets and helpers shared by the tabs and the shell."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
    """A table whose rows are Wikipedia pages. The shell asks it for the page under the cursor (the ``w`` key)."""

    def __init__(self, resolve: Callable[[str], tuple[str, str] | None], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._resolve = resolve

    def page_at_cursor(self) -> tuple[str, str] | None:
        """``(wiki, title)`` of the row under the cursor, or None when the table is empty."""
        key = self.cursor_key()
        return None if key is None else self._resolve(key)

    def cursor_key(self) -> str | None:
        if not self.row_count:
            return None
        return self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value
