"""Rolling numbers for the Live tab: edits per minute, a sparkline series, the most edited articles and the
people/bots split. No Textual in here; time comes from the injected clock.

Windows run on arrival time (the clock's ``monotonic``), not on the edit's own timestamp, because event timestamps can
be out of order by a few seconds. Only the windows are kept, never every event.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import UTC, datetime

from wikihose.clock import Clock
from wikihose.sources.events import Edit
from wikihose.store import Store
from wikihose.titles import page_key

MINUTE = 60.0
BUCKET_SECONDS = 10.0
BUCKET_COUNT = 60  # ten minutes of ten-second buckets
TOP_WINDOW = 15 * 60.0
FLUSH_SECONDS = 60.0
KEEP_HOURS = 24


def _minute_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M")


class LiveStats:
    def __init__(self, wiki: str, clock: Clock) -> None:
        self.wiki = wiki
        self._clock = clock
        self._arrivals: deque[float] = deque()  # arrival times in the last minute
        self._buckets: Counter[int] = Counter()  # bucket index -> edits
        self._window: deque[tuple[float, str, bool, bool]] = deque()  # (arrival, title, bot, article)
        self._titles: Counter[str] = Counter()  # articles in the window, by normalized title
        self._latest: dict[str, Edit] = {}  # newest edit of each title in the counter
        self._people = 0
        self._bots = 0
        self._pending: dict[tuple[str, str], list[int]] = {}  # (title, UTC minute) -> counters not yet in the store
        self._last_flush = clock.monotonic()
        self.total = 0  # every edit fed in, for the headless summary

    def add(self, edit: Edit) -> None:
        """Count one edit of the selected wiki, whatever the UI filters are."""
        now = self._clock.monotonic()
        self._expire(now)
        self.total += 1
        self._arrivals.append(now)
        self._buckets[int(now // BUCKET_SECONDS)] += 1
        title = page_key(edit.wiki, edit.title)[1]
        article = edit.namespace == 0
        self._window.append((now, title, edit.bot, article))
        if edit.bot:
            self._bots += 1
        else:
            self._people += 1
        if article:
            self._titles[title] += 1
            self._latest[title] = edit
            self._count_for_store(edit, title)

    def _expire(self, now: float) -> None:
        while self._arrivals and self._arrivals[0] <= now - MINUTE:
            self._arrivals.popleft()
        oldest = int(now // BUCKET_SECONDS) - BUCKET_COUNT + 1
        for index in [i for i in self._buckets if i < oldest]:
            del self._buckets[index]
        while self._window and self._window[0][0] <= now - TOP_WINDOW:
            _, title, bot, article = self._window.popleft()
            if bot:
                self._bots -= 1
            else:
                self._people -= 1
            if article:
                self._titles[title] -= 1
                if self._titles[title] <= 0:
                    del self._titles[title]
                    del self._latest[title]

    def per_minute(self) -> int:
        """Edits that arrived in the last 60 seconds."""
        self._expire(self._clock.monotonic())
        return len(self._arrivals)

    def series(self) -> list[int]:
        """Edits per ten-second bucket for the last ten minutes, oldest first, 60 values (the last may be partial)."""
        now = self._clock.monotonic()
        self._expire(now)
        newest = int(now // BUCKET_SECONDS)
        return [self._buckets.get(i, 0) for i in range(newest - BUCKET_COUNT + 1, newest + 1)]

    def top(self, count: int) -> list[tuple[str, int]]:
        """The most edited articles of the last 15 minutes: (title, edits), ties broken by title."""
        self._expire(self._clock.monotonic())
        ranked = sorted(self._titles.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:count]

    def latest(self, title: str) -> Edit | None:
        """The newest edit of an article still in the top window."""
        return self._latest.get(title)

    def split(self) -> tuple[int, int]:
        """(people, bots) edits over the last 15 minutes, all namespaces."""
        self._expire(self._clock.monotonic())
        return self._people, self._bots

    # --- the store ---------------------------------------------------------------------------------------------------

    def _count_for_store(self, edit: Edit, title: str) -> None:
        minute = self._clock.now().strftime("%Y-%m-%dT%H:%M")
        row = self._pending.setdefault((title, minute), [0, 0, 0, 0])
        row[0] += 1
        row[1] += 0 if edit.bot else 1
        row[2] += max(edit.delta, 0)
        row[3] += max(-edit.delta, 0)

    def maybe_flush(self, store: Store | None) -> bool:
        """Flush when a minute has passed since the last flush. Returns whether it did."""
        if store is None or self._clock.monotonic() - self._last_flush < FLUSH_SECONDS:
            return False
        self.flush(store)
        return True

    def flush(self, store: Store | None) -> None:
        """Write the per-page counts to the store and drop rows older than 24 hours."""
        self._last_flush = self._clock.monotonic()
        if store is None:
            self._pending.clear()
            return
        for (title, minute), (edits, human, added, removed) in self._pending.items():
            store.add_edit_minute(
                self.wiki, title, minute, edits=edits, human_edits=human, bytes_added=added, bytes_removed=removed
            )
        self._pending.clear()
        store.prune_edit_minutes(_minute_of(self._clock.now().timestamp() - KEEP_HOURS * 3600))
