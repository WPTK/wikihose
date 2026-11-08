"""The live data source: the Wikimedia recentchange stream, with reconnects, back-off and health statuses.

``LiveSource`` yields a ``FeedStatus`` first, then parsed edits and page moves for every wiki (the UI filters by
wiki). When the connection drops it says so, waits (1, 2, 4 ... 60 seconds, on the injected clock) and reconnects with
the last event ID, so Wikimedia resumes where the stream left off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wikihose.clock import Clock
from wikihose.net import Client, FetchError
from wikihose.sources.events import FeedState, FeedStatus, StreamItem, parse_event
from wikihose.sources.recording import Recorder

RECENTCHANGE_URL = "https://stream.wikimedia.org/v2/stream/recentchange"
BACKOFF_START = 1.0
BACKOFF_CAP = 60.0
BAD_DATA_REPORT_SECONDS = 60.0
RATE_LIMIT_MIN_WAIT = 5.0


class LiveSource:
    name = "live"

    def __init__(
        self, client: Client, clock: Clock, url: str = RECENTCHANGE_URL, recorder: Recorder | None = None
    ) -> None:
        self._client = client
        self.clock = clock
        self._url = url
        self._recorder = recorder
        self._closed = False

    def _status(self, state: FeedState, detail: str = "", retry_in: float | None = None) -> FeedStatus:
        return FeedStatus("stream", state, detail=detail, retry_in=retry_in, at=self.clock.now())

    async def items(self) -> AsyncIterator[StreamItem]:
        yield self._status("connecting")
        state: FeedState = "connecting"
        backoff = BACKOFF_START
        last_id: str | None = None
        bad = 0  # malformed events not yet reported
        last_report: float | None = None
        while not self._closed:
            wait: float
            try:
                async for event in self._client.stream(self._url, last_id):
                    backoff = BACKOFF_START
                    if event.id:
                        last_id = event.id
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
                wait = backoff
                backoff = min(backoff * 2, BACKOFF_CAP)
                state = "reconnecting"
                yield self._status("reconnecting", "stream ended", retry_in=wait)
            except FetchError as exc:
                if exc.kind == "rate_limited":
                    wait = max(exc.retry_after or 0.0, RATE_LIMIT_MIN_WAIT)
                    state = "rate_limited"
                    yield self._status("rate_limited", exc.detail, retry_in=wait)
                else:
                    wait = backoff
                    backoff = min(backoff * 2, BACKOFF_CAP)
                    state = "reconnecting"
                    yield self._status("reconnecting", exc.detail, retry_in=wait)
            await self.clock.sleep(wait)

    async def aclose(self) -> None:
        self._closed = True
        if self._recorder is not None:
            self._recorder.close()
