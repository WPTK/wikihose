"""What the reader does is still there after quitting and starting again, in the demo, on the real program.

Each run has its own config and data directories (``start(home=...)``), so the reader's own files are never touched.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_journeys import EDIT_ROW, Start, filter_line
from uat_kit import Screen

WATCHED = re.compile(r"Watching (\S.*?)(?:\s{2,}|$)")


def watched_title(screen: Screen) -> str | None:
    for line in screen.lines:
        found = WATCHED.search(line)
        if found:
            return found.group(1)
    return None


def test_a_watched_page_and_a_setting_are_still_there_after_a_restart(start: Start, tmp_path: Path) -> None:
    term = start(home=tmp_path)
    term.wait_for(lambda s: bool(EDIT_ROW.search(s.text)), 20, "edits in the log")
    term.send_keys("w")
    shown = term.wait_for(lambda s: watched_title(s) is not None, 10, "the notice that a page is watched")
    title = watched_title(shown)
    assert title
    term.send_keys("b")
    term.wait_for(lambda s: "bots shown" in filter_line(s), 10, "bots shown after b")
    term.settle()
    term.send_keys("q")
    assert term.wait_exit(10) == 0, term.screen_text()

    again = start(home=tmp_path)
    assert "bots shown" in filter_line(again.screen()), again.screen_text()
    again.send_keys("3")
    again.wait_for(
        lambda s: title in s and "You are not watching any pages yet" not in s, 10, f"{title} on the Watchlist"
    )
    again.send_keys("q")
    assert again.wait_exit(10) == 0, again.screen_text()
