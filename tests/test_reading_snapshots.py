"""Snapshots of the Reading tab fed a fixed list of rows (no network). Update with ``--snapshot-update`` and read each SVG."""

from __future__ import annotations

from typing import Any

import pytest
from support import ListSource, make_app
from test_reading_tab import FakeReading, settle_reading

from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.reading import SpikeRow, Summary
from wikihose.ui.tabs.reading import ReadingTab

CLOCK_START = __import__("datetime").datetime(2026, 11, 7, 12, 0, tzinfo=__import__("datetime").UTC)
SERIES = (120, 130, 110, 100, 140, 120, 125, 118, 133, 129, 111, 105, 99, 140, 150, 120, 115, 130, 128, 119, 121, 117,
          124, 126, 131, 122, 118, 127, 600, 9000)  # fmt: skip


class SnapshotReading(FakeReading):
    async def summary(self, title: str) -> Summary | None:
        return Summary(
            title.replace("_", " "),
            "Indian epic film",
            "Ramayana: Part 1 is a 2026 Indian epic film retelling the first part of the Ramayana, "
            "directed by Nitesh Tiwari. It was released in theatres on 6 November 2026.",
        )


def rows() -> list[SpikeRow]:
    titles = ["Ramayana:_Part_1", "Stephen_Miller", "Nithya_Raman", "Joe_Fagnano", "Russell_Brand", "Susie_Wiles"]
    return [
        SpikeRow(
            t,
            785245 // (n + 1),
            3200.0 + n * 10,
            785245 / (n + 1) / (3200 + n * 10),
            SERIES if n % 3 else (),
            n % 3 != 0,
        )
        for n, t in enumerate(titles)
    ]


@pytest.mark.parametrize(
    ("theme", "size"),
    [("wikihose-dark", (80, 24)), ("wikihose-dark", (140, 40)), ("wikihose-light", (80, 24))],
    ids=["dark-80x24", "dark-140x40", "light-80x24"],
)
def test_reading_tab(snap_compare: Any, theme: str, size: tuple[int, int]) -> None:
    clock = VirtualClock(CLOCK_START)
    app = make_app(ListSource(clock), clock, config=Config(theme=theme), reading=lambda _wiki: SnapshotReading(rows()))

    async def open_reading(pilot: Any) -> None:
        await pilot.press("2")
        await settle_reading(app, pilot)
        for _ in range(50):
            await pilot.pause()

    assert snap_compare(app, terminal_size=size, run_before=open_reading)
    assert ReadingTab
