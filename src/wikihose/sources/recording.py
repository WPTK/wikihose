"""The recording file format (version 1) and the code that writes and reads it. The format is in docs/replay.md.

A recording is gzipped UTF-8 JSONL: one header line, then one line per server-sent event with its offset in seconds
from the start of the recording. ``Recorder`` writes it (flushing every second of clock time and on close);
``read_recording`` validates the header and skips bad lines, counting them.
"""

from __future__ import annotations

import gzip
import importlib.resources
import json
import math
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from wikihose.clock import Clock
from wikihose.sse import SseEvent

FORMAT = "wikihose-recording"
VERSION = 1
FLUSH_SECONDS = 1.0
HEADER_LIMIT = 64 * 1024  # longest header line read
READ_ERRORS = (EOFError, OSError, UnicodeDecodeError, zlib.error)  # a damaged or cut-off file


class RecordingError(ValueError):
    """The file is not a readable wikihose recording."""


@dataclass(frozen=True, slots=True)
class RecordingHeader:
    recorded_at: datetime
    stream: str
    wiki: str


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    t: float  # seconds since the recording started
    id: str | None
    data: str


class Recorder:
    """Writes a v1 recording. Give ``wiki`` to keep only that wiki's events (by their JSON ``wiki`` field)."""

    def __init__(self, path: Path, clock: Clock, wiki: str | None = None) -> None:
        self._clock = clock
        self._wiki = wiki
        self._start = clock.monotonic()
        self._last_flush = self._start
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] | None = gzip.open(path, "wt", encoding="utf-8")  # noqa: SIM115  (held open until close())
        header = {
            "format": FORMAT,
            "version": VERSION,
            "recorded_at": clock.now().astimezone(UTC).isoformat(timespec="seconds"),
            "stream": "recentchange",
            "wiki": wiki or "all",
        }
        self._file.write(json.dumps(header) + "\n")
        self._file.flush()

    def write(self, event: SseEvent) -> None:
        if self._file is None:
            return
        if self._wiki is not None and not self._matches(event.data):
            return
        now = self._clock.monotonic()
        line = {"t": round(now - self._start, 3), "id": event.id, "data": event.data}
        self._file.write(json.dumps(line) + "\n")
        if now - self._last_flush >= FLUSH_SECONDS:
            self._file.flush()
            self._last_flush = now

    def _matches(self, data: str) -> bool:
        try:
            parsed = json.loads(data)
        except ValueError:
            return False
        return isinstance(parsed, dict) and parsed.get("wiki") == self._wiki

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


class RecordedEvents:
    """The events of a recording, in order. ``skipped`` counts the bad lines passed over so far."""

    def __init__(self, lines: Iterator[str], handle: IO[str]) -> None:
        self._lines = lines
        self._handle = handle
        self.skipped = 0

    def __iter__(self) -> Iterator[RecordedEvent]:
        return self

    def close(self) -> None:
        self._handle.close()

    def __next__(self) -> RecordedEvent:
        while True:
            try:
                line = next(self._lines)
            except StopIteration:
                self._handle.close()
                raise
            except READ_ERRORS:  # a recording cut off mid-write, or damaged
                self.skipped += 1
                self._handle.close()
                raise StopIteration from None
            event = _event_of(line)
            if event is None:
                self.skipped += 1
                continue
            return event


def _event_of(line: str) -> RecordedEvent | None:
    if not line.strip():
        return None
    try:
        raw = json.loads(line)
        t, id_, data = raw["t"], raw.get("id"), raw["data"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if isinstance(t, bool) or not isinstance(t, int | float) or not isinstance(data, str):
        return None
    if not math.isfinite(t) or t < 0:
        return None
    if id_ is not None and not isinstance(id_, str):
        return None
    return RecordedEvent(t=float(t), id=id_, data=data)


def read_recording(path: Path) -> tuple[RecordingHeader, RecordedEvents]:
    """Open a recording; raises ``RecordingError`` when the header is missing or not a v1 wikihose header."""
    try:
        handle = gzip.open(path, "rt", encoding="utf-8")  # noqa: SIM115  (closed by RecordedEvents)
    except READ_ERRORS as exc:
        raise RecordingError(f"cannot read {path}: {exc}") from exc
    try:
        first = handle.readline(HEADER_LIMIT)
    except READ_ERRORS as exc:
        handle.close()
        raise RecordingError(f"cannot read {path}: {exc}") from exc
    if len(first) >= HEADER_LIMIT and not first.endswith("\n"):
        handle.close()
        raise RecordingError(f"{path} has no recording header")
    try:
        raw = json.loads(first)
        if raw["format"] != FORMAT or raw["version"] != VERSION:
            raise RecordingError(f"{path} is not a version {VERSION} wikihose recording")
        recorded_at = datetime.fromisoformat(raw["recorded_at"])
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        header = RecordingHeader(recorded_at.astimezone(UTC), str(raw["stream"]), str(raw["wiki"]))
    except RecordingError:
        handle.close()
        raise
    except (ValueError, KeyError, TypeError) as exc:
        handle.close()
        raise RecordingError(f"{path} has no valid recording header") from exc
    return header, RecordedEvents(iter(handle), handle)


@contextmanager
def sample_recording() -> Iterator[Path]:
    """The sample recording that ships in the wheel (``wikihose/data/sample.jsonl.gz``), as a real file path."""
    with importlib.resources.as_file(importlib.resources.files("wikihose") / "data" / "sample.jsonl.gz") as path:
        yield path
