"""Snapshots of the Convergence tab fed fixed edits and a fixed ranking (no network).

Update with ``--snapshot-update`` and read each SVG.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from support import ListSource, make_app, make_edit
from test_convergence import FakeReading, open_tab, spike

from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.convergence import SURGE_EDITS
from wikihose.sources.events import StreamItem

CLOCK_START = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def fake() -> FakeReading:
    return FakeReading(
        [spike("Ramayana:_Part_1", 245.4, 785245), spike("Stephen_Miller", 61.2), spike("Nithya_Raman", 18.0)],
        {"Hurricane_Melissa_(2026)": spike("Hurricane_Melissa_(2026)", 37.5, 150000)},
    )


def items() -> list[StreamItem]:
    plan = [
        ("Ramayana: Part 1", 4),
        ("Stephen Miller", 7),
        ("Nithya Raman", 2),
        ("Hurricane Melissa (2026)", SURGE_EDITS),
    ]
    return [make_edit(title) for title, count in plan for _ in range(count)]


@pytest.mark.parametrize(
    ("theme", "size"),
    [("wikihose-dark", (80, 24)), ("wikihose-dark", (120, 30)), ("wikihose-light", (80, 24))],
    ids=["dark-80x24", "dark-120x30", "light-80x24"],
)
def test_convergence_tab(snap_compare: Any, theme: str, size: tuple[int, int]) -> None:
    clock = VirtualClock(CLOCK_START)
    reading = fake()
    app = make_app(ListSource(clock, items()), clock, config=Config(theme=theme), reading=lambda _wiki: reading)

    async def open_convergence(pilot: Any) -> None:
        await open_tab(app, pilot)
        for _ in range(50):
            await pilot.pause()

    assert snap_compare(app, terminal_size=size, run_before=open_convergence)


def test_convergence_tab_empty(snap_compare: Any) -> None:
    clock = VirtualClock(CLOCK_START)
    app = make_app(ListSource(clock), clock, reading=None)

    async def open_convergence(pilot: Any) -> None:
        await open_tab(app, pilot)

    assert snap_compare(app, terminal_size=(80, 24), run_before=open_convergence)
