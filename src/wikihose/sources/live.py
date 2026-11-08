"""The live data source: the Wikimedia recentchange stream, with reconnects, back-off and health statuses.

``LiveSource`` yields a ``FeedStatus`` first, then parsed edits and page moves for every wiki (the UI filters by
wiki). When the connection drops it says so, waits (1, 2, 4 ... 60 seconds, on the injected clock) and reconnects with
the last event ID, so Wikimedia resumes where the stream left off.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable
from typing import Any

from wikihose.clock import Clock
from wikihose.net import Client, FetchError
from wikihose.sources.events import FeedState, FeedStatus, StreamItem, parse_event
from wikihose.sources.recording import Recorder
from wikihose.sse import OVERSIZE, SseEvent, valid_event_id

RECENTCHANGE_URL = "https://stream.wikimedia.org/v2/stream/recentchange"
BACKOFF_START = 1.0
BACKOFF_CAP = 60.0
STABLE_SECONDS = 60.0  # a connection must stay up this long (clock time) before the back-off starts over
BAD_DATA_REPORT_SECONDS = 60.0
RATE_LIMIT_MIN_WAIT = 5.0

_STOPPED: Any = object()  # what a raced wait returns when aclose() cut it short
_ENDED: Any = object()  # the end of the event stream


class LiveSource:
    """The recentchange stream as a DataSource.

    ``aclose()`` stops an in-progress read or back-off wait and ends ``items()``. The UI should still cancel the worker
    that is iterating ``items()``, as it would for any other source.
    """

    name = "live"

    def __init__(
        self, client: Client, clock: Clock, url: str = RECENTCHANGE_URL, recorder: Recorder | None = None
    ) -> None:
        self._client = client
        self.clock = clock
        self._url = url
        self._recorder = recorder
        self._stop = asyncio.Event()

    def _status(self, state: FeedState, detail: str = "", retry_in: float | None = None) -> FeedStatus:
        return FeedStatus("stream", state, detail=detail, retry_in=retry_in, at=self.clock.now())

    async def _race(self, awaitable: Awaitable[Any]) -> Any:
        """Await ``awaitable`` unless ``aclose()`` comes first, in which case cancel it and return ``_STOPPED``."""
        task = asyncio.ensure_future(awaitable)
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                return task.result()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return _STOPPED
        except BaseException:
            task.cancel()
            raise
        finally:
            stopper.cancel()

    async def _next(self, stream: AsyncIterator[SseEvent]) -> Any:
        try:
            return await anext(stream)
        except StopAsyncIteration:
            return _ENDED

    async def items(self) -> AsyncIterator[StreamItem]:
        yield self._status("connecting")
        state: FeedState = "connecting"
        backoff = BACKOFF_START
        last_id: str | None = None
        bad = 0  # malformed events not yet reported
        last_report: float | None = None
        while not self._stop.is_set():
            wait: float
            started = self.clock.monotonic()
            stream = self._client.stream(self._url, last_id)
            try:
                try:
                    while True:
                        event = await self._race(self._next(stream))
                        if event is _STOPPED:
                            return
                        if event is _ENDED:
                            break
                        if valid_event_id(event.id):
                            last_id = event.id
                        if event.event == OVERSIZE:
                            item, well_formed = None, False
                        else:
                            if self._recorder is not None:
                                self._recorder.write(event)
                            item, well_formed = parse_event(event.data)
                        if not well_formed:
                            bad += 1
                            now = self.clock.monotonic()
                            if last_report is None or now - last_report >= BAD_DATA_REPORT_SECONDS:
                                last_report = now
                                state = "bad_data"
                                yield self._status("bad_data", f"{bad} event{'s' if bad != 1 else ''}")
                                bad = 0
                            continue
                        if state != "ok":
                            state = "ok"
                            yield self._status("ok")
                        if item is not None:
                            yield item
                finally:
                    await stream.aclose()
                if self.clock.monotonic() - started >= STABLE_SECONDS:
                    backoff = BACKOFF_START
                wait = backoff
                backoff = min(backoff * 2, BACKOFF_CAP)
                state = "reconnecting"
                yield self._status("reconnecting", "stream ended", retry_in=wait)
            except FetchError as exc:
                if self.clock.monotonic() - started >= STABLE_SECONDS:
                    backoff = BACKOFF_START
                if exc.kind == "rate_limited":
                    wait = max(exc.retry_after or 0.0, RATE_LIMIT_MIN_WAIT)
                    state = "rate_limited"
                    yield self._status("rate_limited", exc.detail, retry_in=wait)
                else:
                    if exc.kind == "bad_data":
                        last_id = None  # the server rejected or garbled the request; resume from scratch
                    wait = backoff
                    backoff = min(backoff * 2, BACKOFF_CAP)
                    state = "reconnecting"
                    yield self._status("reconnecting", exc.detail, retry_in=wait)
            if await self._race(self.clock.sleep(wait)) is _STOPPED:
                return

    async def aclose(self) -> None:
        self._stop.set()
        if self._recorder is not None:
            self._recorder.close()
