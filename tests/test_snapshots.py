"""Snapshots of the Live tab fed the packaged sample recording on a virtual clock, run to its end (New York time).

Update with ``python -m pytest --snapshot-update`` after an intentional change, and read every changed SVG.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from support import EASTERN, live_tab, wait_for_stream

from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.sources.events import FeedStatus
from wikihose.sources.recording import read_recording, sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.ui.app import WikihoseApp

Before = Callable[[Any], Awaitable[None]]


def sample_app(theme: str) -> WikihoseApp:
    with sample_recording() as path:
        header, _ = read_recording(path)
        clock = VirtualClock(header.recorded_at)
        source = ReplaySource(path, clock)
    return WikihoseApp(source, Config(theme=theme), clock=clock, tz=EASTERN)


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
