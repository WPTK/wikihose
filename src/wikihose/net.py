"""The one shared HTTP client: User-Agent, request budget, retries, a disk cache and the event-stream connection.

Wikimedia allows 200 requests a minute to a client with a compliant User-Agent, at most 3 in flight (see
docs/data-sources.md). ``RequestBudget`` keeps the whole app well under that and carries a shared back-off, so a 429
makes every caller wait. ``Client`` is the only thing that talks to the network; it turns every failure into a
``FetchError`` a screen can describe in plain words. All waiting goes through the injected clock.
"""

# Patterns (user_agent, retry_after, the polite request budget idea) adapted from earthwave
# (https://github.com/WPTK/earthwave) net.py at commit 2b4fe0a49844619ae217f6eec02061b6bc2d5830.

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, closing
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal

import hishel
import hishel.httpx
import httpx
import stamina

from wikihose import __version__
from wikihose.clock import Clock
from wikihose.sse import SseEvent, parse_sse, valid_event_id

log = logging.getLogger(__name__)

REPO_URL = "https://github.com/WPTK/wikihose"
RETRY_DEFAULT = 5.0  # seconds to wait when a server gives no usable Retry-After
RETRY_CAP = 300.0
TRIES = 3
WINDOW = 60.0
CACHE_FILE = "http-cache.db"
CACHE_MAX_BYTES = 256 * 1024 * 1024

FetchKind = Literal["down", "rate_limited", "bad_data"]


class FetchError(Exception):
    """A request failed in a way the UI can describe. ``retry_after`` is set when a server asked us to wait."""

    def __init__(self, kind: FetchKind, detail: str, retry_after: float | None = None) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind: FetchKind = kind
        self.detail = detail
        self.retry_after = retry_after


def user_agent(contact: str = "") -> str:
    suffix = f"; {contact}" if contact else ""
    return f"wikihose/{__version__} ({REPO_URL}{suffix}) httpx/{httpx.__version__}"


def retry_after(header: str | None, now: datetime, default: float = RETRY_DEFAULT) -> float:
    """Seconds to wait from a Retry-After header (a number or an HTTP date). Absent or invalid: ``default``, never
    less than 5 seconds."""
    floor = max(default, RETRY_DEFAULT)
    if not header or not header.strip():
        return floor
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return floor
    return max(0.0, (when - now).total_seconds()) if when.tzinfo else floor


class RequestBudget:
    """Rolling-window request limit plus an in-flight cap. One instance per app.

    ``slot()`` waits (on the clock) until a request may start, then holds one of the in-flight places until the block
    ends. ``pause_until`` makes every caller wait, for a shared back-off after a 429 or 503.
    """

    def __init__(self, clock: Clock, per_minute: int = 150, in_flight: int = 3) -> None:
        self._clock = clock
        self._per_minute = per_minute
        self._starts: deque[float] = deque()
        self._semaphore = asyncio.Semaphore(in_flight)
        self._paused_until = 0.0

    def pause_until(self, when: float) -> None:
        """Hold every request until the clock's monotonic time reaches ``when``."""
        self._paused_until = max(self._paused_until, when)

    def _wait_needed(self) -> float:
        now = self._clock.monotonic()
        while self._starts and self._starts[0] + WINDOW <= now + 1e-9:
            self._starts.popleft()
        wait = self._paused_until - now
        if len(self._starts) >= self._per_minute:
            wait = max(wait, self._starts[0] + WINDOW - now)
        return wait

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._semaphore:
            while (wait := self._wait_needed()) > 1e-9:
                await self._clock.sleep(wait)
            self._starts.append(self._clock.monotonic())
            yield

    def stats(self) -> dict[str, float]:
        """``used`` requests in the current window, the ``window`` length and ``paused_until`` (monotonic time)."""
        self._wait_needed()
        return {"used": float(len(self._starts)), "window": WINDOW, "paused_until": self._paused_until}


class _Retry(Exception):
    """Wraps the error of one try so stamina retries it; the last one is unwrapped for the caller."""

    def __init__(self, error: FetchError) -> None:
        super().__init__(str(error))
        self.error = error


def _discard_unreadable(database: Path) -> None:
    """Delete a cache database that is not a readable SQLite file; the cache then starts empty."""
    if not database.exists():
        return
    try:
        with closing(sqlite3.connect(database)) as db:
            if db.execute("PRAGMA quick_check").fetchall() == [("ok",)]:
                return
    except sqlite3.Error:
        pass
    log.warning("discarding the unreadable http cache %s", database)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)


def _open_cache(cache_dir: Path) -> hishel.httpx.AsyncCacheTransport:
    cache_dir.mkdir(parents=True, exist_ok=True)
    database = cache_dir / CACHE_FILE
    _discard_unreadable(database)
    storage = hishel.AsyncSqliteStorage(database_path=database, max_size=CACHE_MAX_BYTES)
    # RFC 9111 mode: honour the server's cache headers. Shared mode, because the responses are meant for CDN-style
    # caches (the pageviews API sends only s-maxage) and wikihose is one more client of that CDN.
    return hishel.httpx.AsyncCacheTransport(
        httpx.AsyncHTTPTransport(), storage=storage, rfc=hishel.CacheOptions(shared=True)
    )


class Client:
    """One ``httpx.AsyncClient`` for the whole app, behind the request budget."""

    def __init__(self, clock: Clock, budget: RequestBudget, contact: str = "", cache_dir: Path | None = None) -> None:
        self._clock = clock
        self._budget = budget
        transport: httpx.AsyncBaseTransport | None = None
        if cache_dir is not None:
            try:
                transport = _open_cache(cache_dir)
            except Exception as exc:  # an unusable cache costs the cache, not the app
                log.warning("http cache unavailable (%s: %s); continuing without it", type(exc).__name__, exc)
        headers = {"User-Agent": user_agent(contact), "Accept-Encoding": "gzip"}
        self._http = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0),
            follow_redirects=True,
        )
        # The event stream gets its own client with no cache: hishel 2.0 ignores a request's no-store, so a stream sent
        # through the caching transport would be written into the cache database.
        self._stream_http = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0),
            follow_redirects=True,
        )

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``url`` and decode the JSON. A 404 gives None. Failures raise ``FetchError``."""
        try:
            async for attempt in stamina.retry_context(on=_Retry, attempts=TRIES, wait_initial=0.5, wait_max=5.0):
                with attempt:
                    return await self._get_once(url, params, attempt.num)
        except _Retry as exc:
            raise exc.error from None
        raise AssertionError("unreachable")  # pragma: no cover  (stamina raises when attempts run out)

    async def _get_once(self, url: str, params: dict[str, Any] | None, number: int) -> Any:
        try:
            async with self._budget.slot():
                response = await self._http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise _Retry(FetchError("down", f"{type(exc).__name__}: {exc}")) from exc
        status = response.status_code
        if status in (429, 503):
            wait = self._back_off(response, number)
            raise _Retry(FetchError("rate_limited", f"HTTP {status}", retry_after=wait))
        if status >= 500:
            raise _Retry(FetchError("down", f"HTTP {status}"))
        if status == 404:
            return None
        if status >= 400:
            raise FetchError("bad_data", f"HTTP {status}")
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError("bad_data", "response was not valid JSON") from exc

    def _back_off(self, response: httpx.Response, number: int) -> float:
        """Read how long the server asked us to wait (else back off exponentially from 5 s) and hold every caller."""
        fallback = min(RETRY_DEFAULT * 2 ** (number - 1), RETRY_CAP)
        wait = min(retry_after(response.headers.get("Retry-After"), self._clock.now(), fallback), RETRY_CAP)
        self._budget.pause_until(self._clock.monotonic() + wait)
        return wait

    async def stream(self, url: str, last_event_id: str | None = None) -> AsyncGenerator[SseEvent]:
        """One long-lived GET that yields server-sent events until the server or the network ends it.

        The connect counts once against the budget; the connection holds no in-flight place while it streams.
        """
        headers = {"Accept": "text/event-stream"}
        if last_event_id and valid_event_id(last_event_id):
            headers["Last-Event-ID"] = last_event_id
        try:
            request = self._stream_http.build_request("GET", url, headers=headers)
        except (ValueError, TypeError, httpx.InvalidURL) as exc:
            raise FetchError("bad_data", f"could not build the request: {exc}") from exc
        try:
            async with self._budget.slot():
                response = await self._stream_http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise FetchError("down", f"{type(exc).__name__}: {exc}") from exc
        try:
            status = response.status_code
            if status in (429, 503):
                wait = self._back_off(response, 1)
                raise FetchError("rate_limited", f"HTTP {status}", retry_after=wait)
            if status >= 500:
                raise FetchError("down", f"HTTP {status}")
            if status >= 400:
                raise FetchError("bad_data", f"HTTP {status}")
            kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if kind != "text/event-stream":
                raise FetchError("bad_data", f"expected text/event-stream, got {kind or 'no content type'}")
            async for event in parse_sse(response.aiter_text()):
                yield event
        except httpx.HTTPError as exc:
            raise FetchError("down", f"{type(exc).__name__}: {exc}") from exc
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self._stream_http.aclose()
        await self._http.aclose()
