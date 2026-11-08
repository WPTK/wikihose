"""The replay data source: plays a recording back at its recorded pace, with pause, speed and skip.

``ReplaySource`` is a DataSource, so the rest of the app cannot tell it from the live stream. Waiting goes through the
injected clock (a ``VirtualClock`` in tests, so nothing waits on the wall clock). The controls are plain methods meant
to be called from the UI's own event loop thread, as Textual's key handlers are.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

from wikihose.clock import Clock
from wikihose.sources.events import FeedStatus, StreamItem, parse_event
from wikihose.sources.recording import read_recording

MIN_SPEED = 0.5
MAX_SPEED = 16.0
MAX_STEP = 0.5  # longest single wait, in seconds, so pause, speed and skip take effect quickly


class ReplaySource:
    name = "replay"

    def __init__(self, path: Path, clock: Clock, *, speed: float = 1.0) -> None:
        self.clock = clock
        self._path = path
        self._header, self._events = read_recording(path)
        self.recorded_start: datetime = self._header.recorded_at
        self._elapsed = 0.0  # recorded seconds played so far
        self._speed = _clamp(speed)
        self._running = asyncio.Event()
        self._running.set()
        self._closed = False

    @property
    def skipped_lines(self) -> int:
        """Bad lines passed over so far; complete once the replay has finished."""
        return self._events.skipped

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = _clamp(value)

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def skip(self, seconds: float) -> None:
        """Jump forward in recorded time. Events in the skipped span are still yielded, without waiting."""
        if seconds > 0:
            self._elapsed += seconds

    def rewind(self) -> bool:
        """Start the recording over from the top; call it after ``items()`` has finished, then iterate it again.

        Does nothing and returns False once the source is closed.
        """
        if self._closed:
            return False
        self._events.close()
        self._header, self._events = read_recording(self._path)
        self._elapsed = 0.0
        return True

    def moment(self) -> datetime:
        """The recorded time the replay has reached."""
        return self.recorded_start + timedelta(seconds=self._elapsed)

    async def items(self) -> AsyncIterator[StreamItem]:
        yield FeedStatus("stream", "ok", at=self.moment())
        for event in self._events:
            if self._closed:
                break
            while event.t > self._elapsed and not self._closed:
                if self.paused:
                    await self._running.wait()
                    continue
                gap = event.t - self._elapsed
                step = min(gap, MAX_STEP * self._speed)
                await self.clock.sleep(step / self._speed)
                self._elapsed = max(self._elapsed, event.t) if step == gap else self._elapsed + step
            if self._closed:
                break
            item, _ = parse_event(event.data)
            if item is not None:
                yield item
        yield FeedStatus("stream", "down", detail="replay finished", at=self.moment())

    async def aclose(self) -> None:
        self._closed = True
        self._events.close()
        self.resume()  # release a paused items() so it can end


def _clamp(speed: float) -> float:
    return min(max(speed, MIN_SPEED), MAX_SPEED)
