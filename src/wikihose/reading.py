"""The Reading tab's data: daily spike ranking, 30-day history and the article summary.

All requests go through the shared ``net.Client`` (so the request budget, retries and the disk cache apply). The method
is the one tested in docs/data-sources.md, "Reading baselines":

1. Fetch the newest daily top-articles list and the 29 lists before it.
2. Give every article a *bounded* baseline from those lists: the median of its listed days, counting a day it was
   missing from at that day's rank-1,000 view count (it had fewer). That makes the bounded spike a lower bound.
3. Fetch exact per-article history for the best ``exact_count`` candidates and replace the bound with the true median.

Everything that reads a payload is defensive: junk from the network gives fewer rows, never an exception. Only
``FetchError`` (from the client) escapes, so the screen can describe it.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote

from wikihose.clock import Clock
from wikihose.namespaces import NamespaceFilter
from wikihose.net import Client, FetchError

DAYS = 30  # the newest day plus the 29 before it
MIN_VIEWS = 5000  # a page needs this many views on the newest day to count as a spike (owner's choice)
EXACT_COUNT = 50  # per-article history is fetched for this many candidates
MAX_VIEWS = 10**12  # larger than any real count; keeps the median arithmetic in float range
MIN_LISTS = 10  # fewer earlier lists than this and no baseline is trustworthy
LATEST_TRIES = 3  # how many days back to look for the newest top list (a day's list appears hours after it ends)

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class SpikeRow:
    title: str  # underscores, as the API gives it
    views: int  # on the newest day
    baseline: float  # 30-day median (an upper bound unless ``exact``)
    multiple: float  # views / baseline (a lower bound unless ``exact``)
    series: tuple[int, ...]  # daily views, oldest first, ending with ``views``; empty if unknown
    exact: bool  # True when the baseline comes from the article's own history

    @property
    def display_title(self) -> str:
        return self.title.replace("_", " ")


@dataclass(frozen=True)
class Summary:
    title: str
    description: str
    extract: str


def project_of(wiki: str) -> str:
    """The Pageviews project for a wiki database name: ``enwiki`` -> ``en.wikipedia``."""
    if not wiki.endswith("wiki") or len(wiki) <= 4:
        raise ValueError(f"unsupported wiki {wiki!r}")
    return wiki.removesuffix("wiki").replace("_", "-") + ".wikipedia"


def top_url(project: str, day: date) -> str:
    return f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{project}/all-access/{day:%Y/%m/%d}"


def history_url(project: str, title: str, first: date, last: date) -> str:
    return (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/all-access/user/"
        f"{quote(title, safe='')}/daily/{first:%Y%m%d}/{last:%Y%m%d}"
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_VIEWS else None


def _encodable(title: str) -> bool:
    """False for a title with a lone surrogate, which cannot go into a URL."""
    try:
        title.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_top(payload: Any) -> dict[str, int]:
    """Article -> views from a daily top list, in rank order. Anything unusable gives an empty dict."""
    try:
        rows = payload["items"][0]["articles"]
    except (KeyError, IndexError, TypeError):
        return {}
    result: dict[str, int] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("article"), str) and _encodable(row["article"]):
            views = _int(row.get("views"))
            if views is not None and row["article"] not in result:
                result[row["article"]] = views
    return result


def parse_history(payload: Any, first: date, days: int = DAYS) -> list[int]:
    """Daily views for ``days`` days from ``first``, aligned by timestamp.

    The API leaves out days with no data, so a position in ``items`` is not a day: align on the timestamp and fill the
    gaps with zero. Items outside the window or with a bad timestamp are ignored.
    """
    series = [0] * days
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return series
    for item in items:
        if not isinstance(item, dict):
            continue
        stamp, views = item.get("timestamp"), _int(item.get("views"))
        if views is None or not isinstance(stamp, str):
            continue
        try:
            when = datetime.strptime(stamp[:8], "%Y%m%d").date()
        except ValueError:
            continue
        offset = (when - first).days
        if 0 <= offset < days:
            series[offset] = views
    return series


def floor_of(top: Mapping[str, int]) -> int:
    """The view count of the last (rank 1,000) row: an article missing from the list had fewer views than this."""
    return min(top.values()) if top else 0


def bounded_baseline(title: str, earlier: list[Mapping[str, int]]) -> float:
    """Median over the earlier days; a day the title is missing from counts at that day's floor (an upper bound)."""
    if not earlier:
        return 0.0
    return float(statistics.median(day.get(title, floor_of(day)) for day in earlier))


def spike_multiple(views: int, baseline: float) -> float:
    """Views divided by the baseline; a baseline below 1 counts as 1 so a brand-new page does not divide by zero."""
    return views / max(baseline, 1.0)


def parse_summary(payload: Any) -> Summary | None:
    if not isinstance(payload, dict):
        return None
    title, extract = payload.get("title"), payload.get("extract")
    if not isinstance(title, str) or not isinstance(extract, str) or not extract.strip():
        return None
    description = payload.get("description")
    return Summary(title, description if isinstance(description, str) else "", extract.strip())


class ReadingSource(Protocol):
    """What the Reading tab needs; ``ReadingData`` is the real one and tests pass a fake."""

    day: date | None  # the newest day ranked, set by ``load``

    async def load(self, progress: Progress | None = None) -> list[SpikeRow]: ...

    async def summary(self, title: str) -> Summary | None: ...


class ReadingData:
    """Loads and ranks the Reading tab's rows for one wiki."""

    def __init__(
        self,
        client: Client,
        clock: Clock,
        wiki: str = "enwiki",
        *,
        min_views: int = MIN_VIEWS,
        exact_count: int = EXACT_COUNT,
    ) -> None:
        self._client = client
        self._clock = clock
        self.wiki = wiki
        self._project = project_of(wiki)
        self._host = f"{wiki.removesuffix('wiki').replace('_', '-')}.wikipedia.org"
        self._min_views = min_views
        self._exact_count = exact_count
        self.day: date | None = None
        self._filter: NamespaceFilter | None = None
        self._tops: dict[date, dict[str, int]] = {}  # past days never change, so they are kept for the session
        self._histories: dict[tuple[str, date], list[int] | None] = {}  # None: the API has no data for the page
        self._summaries: dict[str, Summary | None] = {}

    async def namespace_filter(self) -> NamespaceFilter:
        if self._filter is None:
            params = {
                "action": "query",
                "meta": "siteinfo",
                "siprop": "namespaces|namespacealiases|general",
                "format": "json",
                "formatversion": "1",
            }
            payload = await self._client.get_json(f"https://{self._host}/w/api.php", params)
            self._filter = NamespaceFilter.from_siteinfo(payload if isinstance(payload, dict) else {})
        return self._filter

    async def _top(self, day: date) -> dict[str, int]:
        if day not in self._tops:
            top = parse_top(await self._client.get_json(top_url(self._project, day)))
            if not top:
                return top  # an empty answer may just be too early; ask again next time
            self._tops[day] = top
        return self._tops[day]

    async def newest_day(self) -> tuple[date, dict[str, int]] | None:
        """The newest day whose top list exists, looking back from yesterday (UTC)."""
        day = self._clock.now().astimezone(UTC).date() - timedelta(days=1)
        for _ in range(LATEST_TRIES):
            top = await self._top(day)
            if top:
                return day, top
            day -= timedelta(days=1)
        return None

    async def load(self, progress: Progress | None = None) -> list[SpikeRow]:
        """The spike ranking, best first. Raises ``FetchError`` when a request fails after its retries."""
        flt = await self.namespace_filter()
        newest = await self.newest_day()
        if newest is None:
            return []
        today, todays = newest
        self.day = today
        first = today - timedelta(days=DAYS - 1)
        earlier_days = [first + timedelta(days=n) for n in range(DAYS - 1)]
        total = len(earlier_days) + self._exact_count
        done = 0
        earlier: list[Mapping[str, int]] = []
        for day in earlier_days:
            top = await self._top(day)
            if top:  # a day with no list is left out of the median rather than counted as nothing
                earlier.append(top)
            done += 1
            if progress:
                progress(done, total)

        if len(earlier) < MIN_LISTS:
            return []  # too few earlier lists for a baseline; an empty tab is better than a false ranking
        candidates = [
            (spike_multiple(views, bounded_baseline(title, earlier)), title, views)
            for title, views in todays.items()
            if views >= self._min_views and flt.is_article(title)
        ]
        candidates.sort(key=lambda c: (-c[0], c[1]))
        total = done + min(len(candidates), self._exact_count)
        rows: list[SpikeRow] = []
        for position, (multiple, title, views) in enumerate(candidates):
            if position >= self._exact_count:
                rows.append(SpikeRow(title, views, bounded_baseline(title, earlier), multiple, (), False))
                continue
            try:
                series = await self.history(title, first)
            except FetchError as exc:
                if exc.kind != "bad_data":  # one odd title must not block the tab; down and throttling still stop it
                    raise
                series = None
            done += 1
            if progress:
                progress(done, total)
            if series is None:
                rows.append(SpikeRow(title, views, bounded_baseline(title, earlier), multiple, (), False))
                continue
            series[-1] = views  # the top list is the figure for the newest day
            baseline = float(statistics.median(series[:-1]))
            rows.append(SpikeRow(title, views, baseline, spike_multiple(views, baseline), tuple(series), True))
        rows.sort(key=lambda r: (-r.multiple, r.title))
        return rows

    async def history(self, title: str, first: date) -> list[int] | None:
        """30 daily views starting at ``first`` for one article, or None when the API has nothing for it."""
        key = (title, first)
        if key not in self._histories:
            last = first + timedelta(days=DAYS - 1)
            payload = await self._client.get_json(history_url(self._project, title, first, last))
            self._histories[key] = None if payload is None else parse_history(payload, first)
        cached = self._histories[key]
        return None if cached is None else list(cached)

    async def summary(self, title: str) -> Summary | None:
        if title not in self._summaries:
            url = f"https://{self._host}/api/rest_v1/page/summary/{quote(title, safe='')}"
            self._summaries[title] = parse_summary(await self._client.get_json(url))
        return self._summaries[title]
