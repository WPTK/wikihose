"""The Reading numbers recorded with the demo, so ``--demo`` fills the Reading and Convergence tabs with no network.

``data/demo-reading-enwiki.json.gz`` holds one ranking as ``ReadingData.load`` returned it for the day before the demo
was recorded, plus the article summaries of the top rows. ``scripts/capture_demo_reading.py`` makes it.
"""

from __future__ import annotations

import gzip
import importlib.resources
import json
from datetime import date
from typing import Any

from wikihose.reading import MAX_VIEWS, Progress, SpikeRow, Summary

DEMO_READING = "demo-reading-enwiki.json.gz"
FORMAT = "wikihose-reading-snapshot"
VERSION = 1
SUMMARY_FIELDS = ("title", "description", "extract")


def _row(item: Any) -> SpikeRow | None:
    """One ranking row from the file, or None when it is not one: the file is package data, but read it defensively."""
    if not isinstance(item, dict):
        return None
    title, views, baseline, multiple = item.get("title"), item.get("views"), item.get("baseline"), item.get("multiple")
    series, exact = item.get("series", []), item.get("exact")
    numbers_ok = isinstance(views, int) and 0 <= views <= MAX_VIEWS and isinstance(baseline, int | float)
    if not (isinstance(title, str) and title and numbers_ok and isinstance(multiple, int | float)):
        return None
    if not isinstance(series, list) or not all(isinstance(v, int) for v in series) or not isinstance(exact, bool):
        return None
    return SpikeRow(title, int(views), float(baseline), float(multiple), tuple(series), exact)  # type: ignore[arg-type]


class DemoReading:
    """A ``ReadingSource`` that serves the recorded ranking. It never touches the network."""

    def __init__(self, payload: Any) -> None:
        payload = payload if isinstance(payload, dict) else {}
        try:
            self._day: date | None = date.fromisoformat(payload.get("day", ""))
        except (TypeError, ValueError):
            self._day = None
        rows = payload.get("rows")
        self._rows = [r for r in (_row(item) for item in (rows if isinstance(rows, list) else [])) if r is not None]
        self._summaries: dict[str, Summary] = {}
        summaries = payload.get("summaries")
        for title, item in (summaries if isinstance(summaries, dict) else {}).items():
            if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in SUMMARY_FIELDS):
                self._summaries[title] = Summary(item["title"], item["description"], item["extract"])
        self.day: date | None = None

    async def load(self, progress: Progress | None = None) -> list[SpikeRow]:
        self.day = self._day
        return list(self._rows) if self._day is not None else []

    async def summary(self, title: str) -> Summary | None:
        return self._summaries.get(title)

    async def spike_of(self, title: str) -> SpikeRow | None:
        return None  # the recording has no surge, and a replay cannot fetch one


def read_demo_reading() -> DemoReading:
    """The bundled snapshot. A damaged file gives an empty ranking, never an exception."""
    resource = importlib.resources.files("wikihose") / "data" / DEMO_READING
    try:
        with resource.open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, EOFError, UnicodeDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or payload.get("version") != VERSION:
        payload = None
    return DemoReading(payload)
