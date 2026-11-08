"""The Convergence tab's numbers: articles hot in both Live and Reading. No Textual in here; time comes from the clock.

An article is on the list when people edited it in the last 15 minutes (Live) and its last full day of reading was at
least ``MIN_SPIKE`` times its usual (Reading). It is ranked by edit velocity times spike multiple.

Reading only ranks pages with thousands of views, so a page people suddenly start editing may not be in it. The surge
trigger covers that: once an article gets ``SURGE_EDITS`` edits by people within the window, the tab fetches that
page's own 30-day history once and gives it a spike multiple of its own.

The windows run on arrival time (the clock's ``monotonic``), like the Live tab's, and only the window is kept.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass

from wikihose.clock import Clock
from wikihose.reading import SpikeRow
from wikihose.sources.events import Edit
from wikihose.titles import page_key

# Each of these is a product choice made in Session 4 for the owner to confirm (#1, Decisions); change it here.
WINDOW = 15 * 60.0  # seconds of live edits that count
SURGE_EDITS = 10  # edits by people within WINDOW that make the tab fetch a page's own pageview history
MIN_SPIKE = 2.0  # "hot in Reading": the last full day had at least this many times the usual views
MIN_SURGE_VIEWS = 1000  # a surge row needs this many views on that day, so a tiny or brand-new page cannot top the list
LABEL = "live edits vs. the last full day of reading"


@dataclass(frozen=True)
class ConvergenceRow:
    title: str  # normalized, with spaces
    edits: int  # edits by people in the window: the edit velocity
    spike: SpikeRow  # the reading numbers
    surge: bool  # True when the reading numbers came from the surge trigger, not the day's Reading ranking

    @property
    def score(self) -> float:
        return self.edits * self.spike.multiple


class Convergence:
    """Counts live edits by people per article and joins them with the reading numbers."""

    def __init__(
        self, wiki: str, clock: Clock, *, surge_edits: int = SURGE_EDITS, min_spike: float = MIN_SPIKE
    ) -> None:
        self.wiki = wiki
        self._clock = clock
        self._surge_edits = surge_edits
        self._min_spike = min_spike
        self._window: deque[tuple[float, str]] = deque()  # (arrival, title) of each edit by a person to an article
        self._edits: Counter[str] = Counter()
        self._reading: dict[str, SpikeRow] = {}  # from the day's Reading ranking
        self._surged: dict[str, SpikeRow | None] = {}  # from the surge trigger; None: asked, nothing came back
        self._asked: set[str] = set()  # every title the trigger has ever queued, so it fetches each one once
        self._queue: deque[str] = deque()

    def add(self, edit: Edit) -> None:
        """Count one edit from the stream. Bots, other wikis and pages outside the article namespace are ignored."""
        if edit.wiki != self.wiki or edit.bot or edit.namespace != 0:
            return
        now = self._clock.monotonic()
        self._expire(now)
        title = page_key(edit.wiki, edit.title)[1]
        self._window.append((now, title))
        self._edits[title] += 1
        if self._edits[title] >= self._surge_edits and title not in self._reading and title not in self._asked:
            self._asked.add(title)
            self._queue.append(title)

    def _expire(self, now: float) -> None:
        while self._window and self._window[0][0] <= now - WINDOW:
            _, title = self._window.popleft()
            self._edits[title] -= 1
            if self._edits[title] <= 0:
                del self._edits[title]

    def set_reading(self, rows: Iterable[SpikeRow]) -> None:
        """The day's Reading ranking. A title already in it never needs a surge fetch."""
        self._reading = {page_key(self.wiki, row.title)[1]: row for row in rows}

    def next_surge(self) -> str | None:
        """The next title the surge trigger wants history for, or None.

        A title is fetched once per run, unless it stopped surging while it waited in the queue (the tab was not open
        yet, or the fetches were paused): then it is dropped and may be queued again by a later surge.
        """
        self._expire(self._clock.monotonic())
        while self._queue:
            title = self._queue.popleft()
            if title in self._reading:
                continue
            if self._edits.get(title, 0) < self._surge_edits:
                self._asked.discard(title)
                continue
            return title
        return None

    @property
    def pending(self) -> bool:
        """True when titles wait in the surge queue."""
        return bool(self._queue)

    def requeue(self, title: str) -> None:
        """Put a title back at the front, for a fetch that was rate limited and will be tried again."""
        self._queue.appendleft(title)

    def add_surge(self, title: str, row: SpikeRow | None) -> None:
        self._surged[title] = row

    @property
    def surges(self) -> int:
        """How many articles the trigger has fetched history for."""
        return len(self._surged)

    def edits(self, title: str) -> int:
        self._expire(self._clock.monotonic())
        return self._edits.get(title, 0)

    def ranked(self) -> list[ConvergenceRow]:
        """The articles hot in both, best first: edits by people in the window times the spike multiple."""
        self._expire(self._clock.monotonic())
        rows = []
        for title, edits in self._edits.items():
            spike = self._reading.get(title)
            surge = spike is None
            if spike is None:
                spike = self._surged.get(title)
                if spike is not None and spike.views < MIN_SURGE_VIEWS:
                    continue
            if spike is not None and spike.multiple >= self._min_spike:
                rows.append(ConvergenceRow(title, edits, spike, surge))
        rows.sort(key=lambda r: (-r.score, r.title))
        return rows
