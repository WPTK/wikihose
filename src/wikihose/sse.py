"""A server-sent events parser (WHATWG HTML spec, "Server-sent events"), kept apart from the HTTP client.

Feed it text (``feed_text``, as the HTTP body arrives) or whole lines (``feed``) and it yields ``SseEvent``s.
Comment lines (starting with ``:``) and ``retry:`` fields are ignored. Several ``data:`` lines in one event are joined
with a newline. A blank line dispatches the event. The ``id`` is remembered across events, as the spec's "last event
ID buffer" is, so an event without its own ``id:`` line carries the last one.

A line or an event over 1 MiB is dropped rather than buffered. The parser then yields an event named ``oversize``
with empty data in its place, so the caller can count it as bad data.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

MAX_LINE = 1024 * 1024
MAX_EVENT = 1024 * 1024
MAX_EVENT_ID = 1000
OVERSIZE = "oversize"
_NEWLINE = re.compile(r"\r\n|\n|\r")
_EVENT_ID = re.compile(r"[\x20-\x7e]*")


@dataclass(frozen=True, slots=True)
class SseEvent:
    id: str | None
    event: str
    data: str


def valid_event_id(value: str | None) -> bool:
    """True for an id that is safe to send back as ``Last-Event-ID``: printable ASCII, at most 1,000 characters."""
    return bool(value) and len(value or "") <= MAX_EVENT_ID and _EVENT_ID.fullmatch(value or "") is not None


class SseParser:
    def __init__(self) -> None:
        self._last_id: str | None = None
        self._event = ""
        self._data: list[str] = []
        self._size = 0
        self._overflow = False
        self._pending = ""  # text received after the last complete line
        self._discarding = False  # inside an oversize line, waiting for its end

    def feed_text(self, text: str) -> list[SseEvent]:
        """Take a chunk of the body; return the events it completes."""
        events: list[SseEvent] = []
        self._pending += text
        while match := _NEWLINE.search(self._pending):
            if match.group() == "\r" and match.end() == len(self._pending):
                break  # may be the first half of \r\n
            line, self._pending = self._pending[: match.start()], self._pending[match.end() :]
            if self._discarding:
                self._discarding = False
            elif len(line) > MAX_LINE:
                self._overflow = True
            elif (event := self.feed(line)) is not None:
                events.append(event)
        if len(self._pending) > MAX_LINE:
            self._pending, self._discarding, self._overflow = "", True, True
        return events

    def feed(self, line: str) -> SseEvent | None:
        """Take one line; return the event it completes, if any."""
        line = line.rstrip("\r\n")
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        match field:
            case "data":
                self._size += len(value) + 1
                if self._size > MAX_EVENT:
                    self._overflow = True
                    self._data = []
                elif not self._overflow:
                    self._data.append(value)
            case "event":
                self._event = value
            case "id":
                if "\0" not in value:
                    self._last_id = value
        return None

    def _dispatch(self) -> SseEvent | None:
        data, event, overflow = self._data, self._event or "message", self._overflow
        self._data, self._event, self._size, self._overflow = [], "", 0, False
        if overflow:
            return SseEvent(id=self._last_id, event=OVERSIZE, data="")
        if not data:
            return None
        return SseEvent(id=self._last_id, event=event, data="\n".join(data))


async def parse_sse(chunks: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Events from an async iterator of text chunks (for example ``response.aiter_text()``)."""
    parser = SseParser()
    async for chunk in chunks:
        for event in parser.feed_text(chunk):
            yield event
