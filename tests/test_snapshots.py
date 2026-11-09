"""Snapshots of the Live tab fed the packaged sample recording on a virtual clock, run to its end (New York time).

Update with ``python -m pytest --snapshot-update`` after an intentional change, and read every changed SVG.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from rich.text import Text
from support import EASTERN, ListSource, live_tab, wait_for_stream

from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.diff import DiffHeader, parse_diff, render_diff
from wikihose.sources.events import FeedStatus
from wikihose.sources.recording import read_recording, sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.store import Store
from wikihose.ui.app import WikihoseApp
from wikihose.ui.tabs.diff import DiffTab
from wikihose.ui.tabs.watchlist import WatchlistTab, trend_key

Before = Callable[[Any], Awaitable[None]]


def sample_app(theme: str) -> WikihoseApp:
    with sample_recording() as path:
        header, _ = read_recording(path)
        clock = VirtualClock(header.recorded_at)
        source = ReplaySource(path, clock)
    return WikihoseApp(source, Config(theme=theme), clock=clock, tz=EASTERN, store=Store(":memory:"))


async def to_the_end(pilot: Any) -> None:
    app = pilot.app
    await wait_for_stream(app, pilot)
    tab = live_tab(app)
    tab.flush_log()
    tab.refresh_side()
    await pilot.pause()


async def with_help(pilot: Any) -> None:
    await to_the_end(pilot)
    await pilot.press("question_mark")
    await pilot.pause()


async def rate_limited(pilot: Any) -> None:
    await to_the_end(pilot)
    app = pilot.app
    app._dispatch(FeedStatus("stream", "rate_limited", retry_in=12, at=app.clock.now()))
    await pilot.pause()


@pytest.mark.parametrize(
    ("theme", "size"),
    [
        ("wikihose-dark", (80, 24)),
        ("wikihose-dark", (120, 30)),
        ("wikihose-dark", (160, 48)),
        ("wikihose-dark", (240, 60)),
        ("wikihose-light", (80, 24)),
        ("wikihose-light", (160, 48)),
    ],
    ids=["dark-80x24", "dark-120x30", "dark-160x48", "dark-240x60", "light-80x24", "light-160x48"],
)
def test_live_tab(snap_compare: Any, theme: str, size: tuple[int, int]) -> None:
    assert snap_compare(sample_app(theme), terminal_size=size, run_before=to_the_end)


def test_help_screen_80x24(snap_compare: Any) -> None:
    assert snap_compare(sample_app("wikihose-dark"), terminal_size=(80, 24), run_before=with_help)


def test_rate_limited_health_bar_80x24(snap_compare: Any) -> None:
    assert snap_compare(sample_app("wikihose-dark"), terminal_size=(80, 24), run_before=rate_limited)


# --- the Watchlist and Diff tabs, on a fixed clock with no network ------------------------------------------------------


def watch_app(*, populated: bool) -> WikihoseApp:
    clock = VirtualClock(datetime(2026, 11, 7, 17, 0, tzinfo=UTC))
    store = Store(":memory:")
    if populated:
        for page_id, wiki, title, seen, last, unseen, minutes in (
            (974, "enwiki", "Ada Lovelace", 100, 104, 3, 40),
            (1208, "enwiki", "Alan Turing", 200, 200, 0, 0),
            (5, "dewiki", "Seite mit einem ziemlich langen Titel", 10, 12, 1, 600),
            (9, "enwiki", "Deleted example", 7, 7, 0, 0),
        ):
            store.add_watch(wiki, page_id, title, clock.now(), seen)
            clock.advance(1)
            edit_at = None if not minutes else clock.now() - timedelta(minutes=minutes)
            store.set_watch_state(
                wiki,
                page_id,
                unseen=unseen,
                last_rev_id=last,
                last_edit_at=edit_at,
                deleted=title.startswith("Deleted"),
            )
    return WikihoseApp(ListSource(clock), Config(theme="wikihose-dark"), clock=clock, tz=EASTERN, store=store)


async def open_tab(pilot: Any, key: str) -> None:
    await pilot.press(key)
    await pilot.pause()
    await pilot.wait_for_animation()  # the tab strip's underline slides to the new tab; capture it at rest
    await pilot.pause()


async def populated_watchlist(pilot: Any) -> None:
    pilot.app._editors[("enwiki", 974)] = "Vitorperrut555"
    pilot.app.watchlist._changed()
    await open_tab(pilot, "3")


async def watchlist_with_trends(pilot: Any) -> None:
    """The first page has a rising 30-day trend, the next a falling one, and any other has no data."""
    await populated_watchlist(pilot)
    tab = pilot.app.query_one(WatchlistTab)
    series = [tuple(range(1, 31)), tuple(range(30, 0, -1))]
    for index, page in enumerate(pilot.app.watchlist.pages()):
        tab._series[trend_key(page)] = series[index] if index < len(series) else None
    tab.refresh_rows()
    await pilot.pause()


async def empty_watchlist(pilot: Any) -> None:
    await open_tab(pilot, "3")
    # Nothing in the empty tab can take the focus, so where it lands after the switch (the tab strip or nowhere)
    # depends on timing. Pin it, or the footer and the tab highlight differ from run to run.
    pilot.app.screen.set_focus(None)
    await pilot.pause()


async def shown_diff(pilot: Any) -> None:
    await open_tab(pilot, "4")
    tab = pilot.app.query_one(DiffTab)
    payload = json.loads((Path(__file__).parent / "fixtures" / "rest" / "compare_changed_line.json").read_text("utf-8"))
    header = DiffHeader(
        "Ada Lovelace", "Vitorperrut555", "Cat-a-lot: moving", datetime(2026, 11, 6, 19, 2, tzinfo=UTC), 98350, 98391
    )
    tab.state = "shown"
    tab._set(render_diff(parse_diff("enwiki", 1383337217, 1384127064, payload), header, tz=EASTERN))
    await pilot.wait_for_animation()
    await pilot.pause()


async def loading_diff(pilot: Any) -> None:
    await open_tab(pilot, "4")
    tab = pilot.app.query_one(DiffTab)
    tab._set(Text("Loading the change…", style="dim"))
    await pilot.pause()


@pytest.mark.parametrize("size", [(80, 24), (120, 30)], ids=["80x24", "120x30"])
def test_watchlist_tab_empty(snap_compare: Any, size: tuple[int, int]) -> None:
    assert snap_compare(watch_app(populated=False), terminal_size=size, run_before=empty_watchlist)


@pytest.mark.parametrize("size", [(80, 24), (120, 30)], ids=["80x24", "120x30"])
def test_watchlist_tab_populated(snap_compare: Any, size: tuple[int, int]) -> None:
    assert snap_compare(watch_app(populated=True), terminal_size=size, run_before=populated_watchlist)


def test_watchlist_tab_with_trends_120x30(snap_compare: Any) -> None:
    assert snap_compare(watch_app(populated=True), terminal_size=(120, 30), run_before=watchlist_with_trends)


def test_diff_tab_empty_80x24(snap_compare: Any) -> None:
    assert snap_compare(watch_app(populated=False), terminal_size=(80, 24), run_before=lambda p: open_tab(p, "4"))


def test_diff_tab_loading_80x24(snap_compare: Any) -> None:
    assert snap_compare(watch_app(populated=False), terminal_size=(80, 24), run_before=loading_diff)


@pytest.mark.parametrize("size", [(80, 24), (120, 30)], ids=["80x24", "120x30"])
def test_diff_tab_shown(snap_compare: Any, size: tuple[int, int]) -> None:
    assert snap_compare(watch_app(populated=False), terminal_size=size, run_before=shown_diff)
