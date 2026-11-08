"""Run a data source without a terminal and summarize it. Used by CI and for checking ``--replay`` by hand."""

from __future__ import annotations

from wikihose.live_stats import LiveStats
from wikihose.sources.events import Edit
from wikihose.sources.protocol import DataSource


async def run_headless(source: DataSource, wiki: str, seconds: float) -> str:
    """Feed ``source`` into LiveStats for ``seconds`` of the source clock's time; return a one-line summary."""
    stats = LiveStats(wiki, source.clock)
    deadline = source.clock.monotonic() + seconds
    try:
        async for item in source.items():
            if isinstance(item, Edit) and item.wiki == wiki:
                stats.add(item)
            if source.clock.monotonic() >= deadline:
                break
    finally:
        await source.aclose()
    top = "; ".join(f"{title} ({count})" for title, count in stats.top(3)) or "none"
    return f"edits={stats.total}, edits_per_min={stats.per_minute()}, top={top}"
