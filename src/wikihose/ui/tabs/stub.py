"""Placeholder panes for the tabs that arrive in later versions."""

from __future__ import annotations

from textual.widgets import Static

LATER = "Arrives in a later version."

# tab id -> (tab label, what the tab will do)
STUBS: dict[str, tuple[str, str]] = {
    "reading": ("Reading", "Daily reading spikes: today's views against each article's 30-day median."),
    "watchlist": ("Watchlist", "Pages you track. Press w to add a page."),
    "diff": ("Diff", "Press Enter on a tracked edit to see what changed, in red and green."),
    "convergence": (
        "Convergence",
        "Articles hot in both Live and Reading: live edits vs. the last full day of reading.",
    ),
}


class StubPane(Static):
    DEFAULT_CSS = """
    StubPane { padding: 1 2; color: $text-muted; height: 1fr; }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"{text}\n\n{LATER}")
