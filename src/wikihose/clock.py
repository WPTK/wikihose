"""The injectable clock. Everything that reads the time or waits does so through a Clock.

``SystemClock`` is the real thing. ``VirtualClock`` only moves when told to (or when something sleeps on it), so
tests and replays never wait on the wall clock. No other module calls ``time.sleep`` or ``asyncio.sleep`` with a
positive delay; a test enforces that.
"""

# Shaped after eyewall (https://github.com/WPTK/eyewall) clock.py at commit 3b4b552767aec9e87e3989c4f64f3f31639398f7.

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary start that never go backwards, for measuring intervals."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait for ``seconds`` of this clock's time."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class VirtualClock:
    """A clock that moves only through ``advance`` and ``sleep``."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("naive datetime; wikihose uses timezone-aware UTC")
        self._now = start.astimezone(UTC)
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a clock cannot advance by a negative amount")
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds

    async def sleep(self, seconds: float) -> None:
        """Advance virtual time by ``seconds``, then yield once so other tasks run."""
        self.advance(max(0.0, seconds))
        await asyncio.sleep(0)
