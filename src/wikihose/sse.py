"""A server-sent events parser (WHATWG HTML spec, "Server-sent events"), kept apart from the HTTP client.

Feed it lines (as httpx's ``aiter_lines`` yields them, line endings already removed) and it yields ``SseEvent``s.
Comment lines (starting with ``:``) and ``retry:`` fields are ignored. Several ``data:`` lines in one event are joined
with a newline. A blank line dispatches the event. The ``id`` is remembered across events, as the spec's "last event
ID buffer" is, so an event without its own ``id:`` line carries the last one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SseEvent:
    id: str | None
    event: str
    data: str


class SseParser:
    def __init__(self) -> None:
        self._last_id: str | None = None
        self._event = ""
        self._data: list[str] = []

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
                self._data.append(value)
            case "event":
                self._event = value
            case "id":
                if "\0" not in value:
                    self._last_id = value
        return None

    def _dispatch(self) -> SseEvent | None:
        data, event = self._data, self._event or "message"
        self._data, self._event = [], ""
        if not data:
            return None
        return SseEvent(id=self._last_id, event=event, data="\n".join(data))


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    parser = SseParser()
    async for line in lines:
        if (event := parser.feed(line)) is not None:
            yield event
