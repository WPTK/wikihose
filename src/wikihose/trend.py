"""The Watchlist tab's 30-day pageview trend for each watched page.

The history comes from ``ReadingData.history`` (the same request, parser and budget the Reading tab uses) and is kept in
the store's ``pageviews`` table, so a restart does not fetch again what is still fresh. Pageview data lags by a day or
more, so the window ends yesterday (UTC), yesterday is only stored once it has views, and a run of zero days at the
end is not drawn: a missing day is not a zero. A page is fresh when its stored days reach back to two days ago, which
means about one fetch per page per day.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta

from wikihose.clock import Clock
from wikihose.net import Client
from wikihose.reading import DAYS, ReadingData
from wikihose.store import Store, WatchedPage
from wikihose.titles import host_for_wiki

LAG_DAYS = 2  # the newest days that may not be published yet, and the most that are not drawn when zero
PAUSE = 1.0  # seconds between two pages' requests: the sparklines fill in slowly and never crowd the budget
FRESH_DAYS = 2  # a page is fresh when its stored days reach this far back from today


class Trends:
    def __init__(self, store: Store, client: Client | None, clock: Clock) -> None:
        """``client`` is None in a replay: stored trends can be read but nothing is fetched."""
        self._store = store
        self._client = client
        self._clock = clock
        self._sources: dict[str, ReadingData] = {}
        self._asked = False  # a request has gone out, so the next one waits

    @staticmethod
    def supported(page: WatchedPage) -> bool:
        """Pageviews exist for Wikipedia editions only; Wiktionary and the rest are skipped."""
        host = host_for_wiki(page.wiki)
        return host is not None and host.endswith(".wikipedia.org")

    def _today(self) -> date:
        return self._clock.now().astimezone(UTC).date()

    def cached(self, page: WatchedPage) -> tuple[tuple[int, ...] | None, bool]:
        """(series, fresh) from the store. The series is None when no day is stored at all."""
        if not self.supported(page):
            return None, True
        today = self._today()
        first = today - timedelta(days=DAYS + LAG_DAYS)
        stored = self._store.pageviews(page.wiki, _key(page), f"{first:%Y-%m-%d}", f"{today:%Y-%m-%d}")
        if not stored:
            return None, False
        newest = date.fromisoformat(max(stored))
        fresh = newest >= today - timedelta(days=FRESH_DAYS)
        days = [newest - timedelta(days=n) for n in range(DAYS - 1, -1, -1)]
        series = [stored.get(f"{d:%Y-%m-%d}", 0) for d in days]
        for _ in range(LAG_DAYS):
            if series[-1] == 0:
                series.pop()
        return tuple(series), fresh

    async def fetch(self, page: WatchedPage) -> tuple[int, ...] | None:
        """The page's last 30 days of views, from the store when fresh, else from Pageviews. None: no data.

        Raises ``FetchError`` when the request fails after its retries.
        """
        series, fresh = self.cached(page)
        if fresh or self._client is None:
            return series
        if self._asked:
            await self._clock.sleep(PAUSE)  # only a request waits; a page served from the store does not
        self._asked = True
        last = self._today() - timedelta(days=1)
        first = last - timedelta(days=DAYS - 1)
        source = self._sources.get(page.wiki)
        if source is None:
            source = self._sources[page.wiki] = ReadingData(self._client, self._clock, page.wiki)
        daily = await source.history(_key(page), first)
        if daily is None:
            return series  # the API has nothing new; whatever is stored (usually nothing) stands
        keep = {}
        for offset, views in enumerate(daily):
            if views or offset < DAYS - 1:
                keep[f"{first + timedelta(days=offset):%Y-%m-%d}"] = views
        self._store.save_pageviews(page.wiki, _key(page), keep)
        return self.cached(page)[0]


def _key(page: WatchedPage) -> str:
    return page.title.replace(" ", "_")
