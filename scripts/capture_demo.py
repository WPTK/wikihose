"""Write docs/images/demo.svg: the Live tab five minutes into the bundled demo recording.

    .venv\\Scripts\\python scripts\\capture_demo.py

The app runs headless under Textual's Pilot, playing the demo recording on a virtual clock that parks at minute 5,
so the numbers are full and two runs give the same bytes. Time zone America/New_York, theme wikihose-dark, 120x30.
Refuses to run with NO_COLOR set, because the picture would come out without colour.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.sources.recording import demo_recording, read_recording
from wikihose.sources.replay import ReplaySource
from wikihose.ui.app import WikihoseApp
from wikihose.ui.bars import TopBar
from wikihose.ui.tabs.live import LiveTab

SIZE = (120, 30)
STOP_AT = 300.0  # seconds of recorded time: the fifth minute
TIMEZONE = ZoneInfo("America/New_York")
TITLE = "wikihose: recorded data, not live"
OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "demo.svg"


def colour_is_off() -> bool:
    """True when NO_COLOR asks for plain output (the convention: set and not empty)."""
    return bool(os.environ.get("NO_COLOR"))


class ParkingClock(VirtualClock):
    """A virtual clock whose sleeps wait forever after ``STOP_AT`` seconds, so the replay stops mid-stream."""

    def __init__(self, start: datetime) -> None:
        super().__init__(start)
        self.parked = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        if self.monotonic() >= STOP_AT:
            self.parked.set()
            await asyncio.Event().wait()  # never set; the app's shutdown cancels this
        await super().sleep(seconds)


async def capture() -> str:
    """Play the demo to minute 5 and return the screen as SVG."""
    with demo_recording() as path:
        header, _ = read_recording(path)
        clock = ParkingClock(header.recorded_at)
        source = ReplaySource(path, clock)
    app = WikihoseApp(source, Config(theme="wikihose-dark", wiki="enwiki"), clock=clock, tz=TIMEZONE, loop_replay=True)
    async with app.run_test(size=SIZE) as pilot:
        await asyncio.wait_for(_until_parked(clock, pilot), timeout=120)
        tab = app.query_one(LiveTab)
        tab.flush_log()
        tab.refresh_side()
        app.query_one(TopBar).refresh_text()
        await pilot.pause()
        return app.export_screenshot(title=TITLE)


async def _until_parked(clock: ParkingClock, pilot: Any) -> None:
    while not clock.parked.is_set():
        await pilot.pause()


def main() -> int:
    if colour_is_off():
        print("capture_demo: NO_COLOR is set, so the picture would have no colour; unset it first", file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(asyncio.run(capture()), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
