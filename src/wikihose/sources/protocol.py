"""The DataSource seam: the one interface the UI and the store see. They cannot tell live from replay."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from wikihose.clock import Clock
from wikihose.sources.events import StreamItem


class DataSource(Protocol):
    name: str  # "live" or "replay"
    clock: Clock

    def items(self) -> AsyncIterator[StreamItem]:
        """Edits, moves and feed status changes. Endless for a live source; a replay ends at the end of the file."""
        ...

    async def aclose(self) -> None:
        """Release whatever the source holds (a recorder, an open file). Safe to call more than once."""
        ...
