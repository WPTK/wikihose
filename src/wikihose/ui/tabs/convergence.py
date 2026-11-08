"""The Convergence tab: articles hot in both Live and Reading, ranked by edit velocity times spike multiple."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Static

from wikihose.clock import Clock
from wikihose.convergence import LABEL, MIN_SPIKE, SURGE_EDITS, WINDOW, Convergence, ConvergenceRow
from wikihose.net import FetchError
from wikihose.reading import ReadingSource, SpikeRow
from wikihose.sources.events import Edit, StreamItem
from wikihose.ui.tabs.reading import spike_text, usual_text
from wikihose.ui.widgets import PageTable

TICK_SECONDS = 2.0  # how often the list is redrawn (edits leave the window as time passes) and the trigger runs
DOWN_PAUSE = 60.0  # after Wikimedia could not be reached, wait this long before the next surge fetch
EDITS_WIDTH = 7
SPIKE_WIDTH = 9
SCORE_WIDTH = 9
FROM_WIDTH = 8
MINUTES = int(WINDOW // 60)


def columns(width: int) -> int:
    """The article column's width for a tab ``width`` cells wide. Nothing scrolls sideways."""
    fixed = EDITS_WIDTH + SPIKE_WIDTH + SCORE_WIDTH + FROM_WIDTH
    return max(width - fixed - 2 * 5 - 2, 10)  # padding of each of the five columns, and the scrollbar


class ConvergenceTab(Vertical):
    DEFAULT_CSS = """
    ConvergenceTab { height: 1fr; }
    ConvergenceTab #convergence-heading { height: 1; padding: 0 1; text-style: bold; }
    ConvergenceTab #convergence-status { height: 1; padding: 0 1; color: $text-muted; }
    ConvergenceTab #convergence-table { height: 1fr; width: 100%; overflow-x: hidden; }
    ConvergenceTab #convergence-empty { height: 1fr; padding: 1 2; color: $text-muted; }
    ConvergenceTab #convergence-detail { height: 3; padding: 0 1; border-top: solid $primary 40%; }
    """

    def __init__(
        self,
        wiki: str,
        clock: Clock,
        subscribe: Callable[[Callable[[StreamItem], None]], None],
        reading_source: Callable[[], ReadingSource | None],
    ) -> None:
        super().__init__(id="convergence-tab")
        self.wiki = wiki
        self._clock = clock
        self._subscribe = subscribe
        self._reading_source = reading_source
        self.model = Convergence(wiki, clock)
        self._rows: list[SpikeRow] = []  # the Reading ranking last received for this wiki
        self._reading_error = ""  # why the Reading numbers did not load, from the Reading tab
        self._reading_seen = False  # a ranking arrived; the trigger waits for it, so it never asks for a ranked page
        self._day: date | None = None
        self._started = False  # the trigger fetches nothing until the tab has been opened once
        self._busy = False
        self._paused_until = 0.0
        self._note = ""  # a pageview error from the trigger
        self._shown: dict[str, ConvergenceRow] = {}

    # --- layout ------------------------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="convergence-heading")
        yield Static(id="convergence-status")
        yield PageTable(self._page_of_row, id="convergence-table", cursor_type="row", zebra_stripes=False)
        yield Static(id="convergence-empty")
        yield Static(id="convergence-detail")

    def on_mount(self) -> None:
        self._subscribe(self.accept)
        self.set_interval(TICK_SECONDS, self.tick)
        self.draw()

    def on_show(self) -> None:
        if not self._started:
            self._started = True
            self.post_message(self.NeedsReading())
            self.tick()
        self.draw()  # the ticks skip drawing while the tab is hidden

    def on_resize(self, event: events.Resize) -> None:
        self.draw()

    class NeedsReading(Message):
        """The tab was opened: the Reading numbers should load if they have not yet."""

    # --- data in -----------------------------------------------------------------------------------------------------

    def accept(self, item: StreamItem) -> None:
        if isinstance(item, Edit):
            self.model.add(item)

    def set_reading(self, wiki: str, rows: list[SpikeRow], error: str = "") -> None:
        """The Reading tab loaded its ranking for ``wiki``, or failed to with ``error`` (it can be loaded again)."""
        if wiki != self.wiki:
            return
        self._reading_error = error
        if not error:
            self._rows = rows
            self.model.set_reading(rows)
            source = self._reading_source()
            self._day = source.day if source is not None else None
            self._reading_seen = True
        self.draw()

    def set_wiki(self, wiki: str) -> None:
        """Follow the Live tab to another wiki: the counts start from zero and the trigger forgets what it fetched."""
        if wiki == self.wiki:
            return
        self.wiki = wiki
        self._rows = []
        self._reading_error = ""
        self._reading_seen = False
        self._day = None
        self.start_over()

    def start_over(self) -> None:
        """The edit counts start from zero (a looping demo began again, or a new wiki); the Reading ranking stays."""
        self.model = Convergence(self.wiki, self._clock)
        self.model.set_reading(self._rows)
        self._note = ""
        self._paused_until = 0.0
        self.draw()

    # --- the surge trigger -------------------------------------------------------------------------------------------

    def tick(self) -> None:
        ready = self._started and self._reading_seen and not self._busy and self.model.pending
        if ready and self._clock.monotonic() >= self._paused_until:
            source = self._reading_source()
            if source is not None and source.day is not None:  # no ranked day: no day to compare a surge with
                self._busy = True
                job = partial(self._fetch_surges, self.model, source)
                self.run_worker(job, name="surge", group="surge")  # type: ignore[arg-type]
        if self.is_on_screen:
            self.draw()

    async def _fetch_surges(self, model: Convergence, source: ReadingSource) -> None:
        """Fetch history for each queued title, one at a time, through the shared client and its request budget."""
        try:
            while model is self.model and (title := model.next_surge()) is not None:
                try:
                    row = await source.spike_of(title)
                except FetchError as error:
                    if model is not self.model:
                        return  # the counts started over while this was fetching: the answer belongs to nothing
                    if error.kind == "bad_data":
                        row = None  # this title only; the next one may be fine
                    else:
                        model.requeue(title)
                        wait = (error.retry_after or 5) if error.kind == "rate_limited" else DOWN_PAUSE
                        self._paused_until = self._clock.monotonic() + wait
                        self._note = (
                            f"Pageviews: rate limited, checking surges again in {int(wait)}s"
                            if error.kind == "rate_limited"
                            else f"Pageviews: could not reach Wikimedia; checking surges again in {int(wait)}s"
                        )
                        return
                if model is not self.model:
                    return
                model.add_surge(title, row)
                self._note = ""
        finally:
            self._busy = False
            self.call_later(self.draw)  # not a direct draw: on quit the worker is cancelled after the widgets are gone

    # --- drawing -----------------------------------------------------------------------------------------------------

    def _page_of_row(self, key: str) -> tuple[str, str] | None:
        return (self.wiki, key) if key in self._shown else None

    def draw(self) -> None:
        width = self.size.width
        if width <= 0:
            return
        rows = self.model.ranked()
        when = f"{self._day:%d %b}".lstrip("0") + " (UTC)" if isinstance(self._day, date) else ""
        heading = f"{LABEL[0].upper()}{LABEL[1:]}" + (f", {when}" if when else "")
        self.query_one("#convergence-heading", Static).update(Text(heading, no_wrap=True, overflow="ellipsis"))
        status = self._note or (
            f"{len(rows)} hot in both · {self.model.surges} surge check"
            + ("" if self.model.surges == 1 else "s")
            + f" · score: edits by people in {MINUTES} min times spike"
        )
        self.query_one("#convergence-status", Static).update(Text(status, no_wrap=True, overflow="ellipsis"))

        table = self.query_one("#convergence-table", PageTable)
        empty = self.query_one("#convergence-empty", Static)
        appearing = bool(rows) and not table.display
        table.display = bool(rows)
        empty.display = not rows
        if appearing and self.screen.focused is None and self.is_on_screen:
            table.focus()  # the tab opened while it was empty, so nothing had the focus
        if not rows:
            self._shown = {}
            table.clear(columns=True)
            empty.update(Text(self._empty_text()))
            self._show_detail(None)
            return
        article = columns(width)
        keep = table.cursor_key()
        scroll = table.scroll_y
        table.clear(columns=True)
        table.add_column("Article", width=article, key="Article")
        table.add_column(Text("Edits", justify="right"), width=EDITS_WIDTH, key="Edits")
        table.add_column(Text("Spike", justify="right"), width=SPIKE_WIDTH, key="Spike")
        table.add_column(Text("Score", justify="right"), width=SCORE_WIDTH, key="Score")
        table.add_column("From", width=FROM_WIDTH, key="From")
        self._shown = {row.title: row for row in rows}
        primary = self.app.current_theme.primary
        for row in rows:
            title = Text(row.title, no_wrap=True)
            title.truncate(article, overflow="ellipsis")
            table.add_row(
                title,
                Text(str(row.edits), justify="right"),
                Text(spike_text(row.spike), justify="right"),
                Text(f"{row.score:,.0f}", justify="right", style=f"bold {primary}"),
                Text("surge" if row.surge else "Reading", style="dim"),
                key=row.title,
            )
        if keep in self._shown:
            table.move_cursor(row=table.get_row_index(keep), animate=False, scroll=False)
            self.call_after_refresh(table.scroll_to, y=scroll, animate=False, immediate=True)
        self._show_detail(table.cursor_key())

    def _empty_text(self) -> str:
        rule = (
            f"An article shows here when people edited it in the last {MINUTES} minutes and its last full day of "
            f"reading was at least {MIN_SPIKE:g} times its usual. When people make {SURGE_EDITS} edits to one "
            f"article in {MINUTES} minutes, its reading history is fetched on its own (a surge)."
        )
        if self._reading_source() is None:
            return (
                "Convergence needs the Reading numbers, and they need a live connection: they are not available in "
                f"a replay or for this wiki.\n\n{rule}"
            )
        if self._reading_error and not self._reading_seen:  # rows from an earlier load still count
            return f"The Reading numbers did not load. The Reading tab (2) says: {self._reading_error}\n\n{rule}"
        if not self._reading_seen:
            return f"Waiting for the Reading numbers…\n\n{rule}"
        if not self._rows and not self.model.surges:
            return f"Reading found no spikes for this day, so only a surge can show here.\n\n{rule}"
        return f"Nothing is hot in both right now.\n\n{rule}"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._show_detail(event.row_key.value)

    def _show_detail(self, title: str | None) -> None:
        detail = self.query_one("#convergence-detail", Static)
        row = self._shown.get(title) if title else None
        if row is None:
            detail.update(Text("Highlight an article to see its numbers. w watches it.", style="dim"))
            return
        spike = row.spike
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(row.title, style="bold")
        where = "a surge: its own 30-day history" if row.surge else "from the Reading ranking"
        text.append(f" · {where}\n", style="dim")
        edits = "edit" if row.edits == 1 else "edits"
        text.append(
            f"{row.edits} {edits} by people in {MINUTES} min · {spike.views:,} views, usual {usual_text(spike)}",
            style="dim",
        )
        detail.update(text)
