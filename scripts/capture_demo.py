"""Write docs/images/demo.svg: the Live tab five minutes into the bundled demo recording.

    .venv\\Scripts\\python scripts\\capture_demo.py [--size WxH] [--tab N] [--out PATH]

The app runs headless under Textual's Pilot, playing the demo recording on a virtual clock that parks at minute 5,
so the numbers are full and two runs give the same bytes. Time zone America/New_York, theme wikihose-dark. With no
options it writes the README picture, 120x30, to docs/images/demo.svg. ``--size`` sets the terminal size (for example
80x24, the floor), ``--tab`` presses the number key of tab 1 to 5 before the capture, and ``--out`` sets the file.
Refuses to run with NO_COLOR set, because the picture would come out without colour.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from wikihose.cli import demo_reading_factory
from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.sources.recording import demo_recording, read_recording
from wikihose.sources.replay import ReplaySource
from wikihose.store import Store
from wikihose.ui.app import WikihoseApp
from wikihose.ui.bars import TopBar
from wikihose.ui.tabs.live import LiveTab

SIZE = (120, 30)
TABS = 5
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


async def capture(size: tuple[int, int] = SIZE, tab: int | None = None) -> str:
    """Play the demo to minute 5 and return the screen as SVG, after opening tab ``tab`` (1 to 5) if given."""
    with demo_recording() as path:
        header, _ = read_recording(path)
        clock = ParkingClock(header.recorded_at)
        source = ReplaySource(path, clock)
    config = Config(theme="wikihose-dark", wiki="enwiki")
    reading = demo_reading_factory()  # what --demo shows on the Reading and Convergence tabs
    app = WikihoseApp(
        source,
        config,
        clock=clock,
        tz=TIMEZONE,
        loop_replay=True,
        reading=reading,
        store=Store(":memory:", history=False),
    )
    async with app.run_test(size=size) as pilot:
        await asyncio.wait_for(_until_parked(clock, pilot), timeout=120)
        live = app.query_one(LiveTab)
        live.flush_log()
        live.refresh_side()
        app.query_one(TopBar).refresh_text()
        if tab is not None:
            await pilot.press(str(tab))
            for _ in range(50):  # the recorded Reading numbers load in a worker; the stream's own never ends
                await pilot.pause()
        await pilot.pause()
        return app.export_screenshot(title=TITLE)


async def _until_parked(clock: ParkingClock, pilot: Any) -> None:
    while not clock.parked.is_set():
        await pilot.pause()


def parse_size(text: str) -> tuple[int, int]:
    """Read ``WxH`` (for example 80x24) into columns and rows."""
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not WIDTHxHEIGHT, such as 80x24") from None
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError("the size must be positive")
    return width, height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_demo", description="Capture the Live tab of the demo as an SVG.")
    parser.add_argument("--size", type=parse_size, default=SIZE, metavar="WxH", help="terminal size (default 120x30)")
    parser.add_argument("--tab", type=int, choices=range(1, TABS + 1), metavar="N", help="open tab 1 to 5 first")
    parser.add_argument("--out", type=Path, default=OUTPUT, metavar="PATH", help="file to write (default demo.svg)")
    args = parser.parse_args(argv)
    if colour_is_off():
        print("capture_demo: NO_COLOR is set, so the picture would have no colour; unset it first", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(asyncio.run(capture(args.size, args.tab)), encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
