"""The Reading tab: the day's biggest reading spikes, a 30-day sparkline for each and the article summary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widgets import DataTable, Static

from wikihose.net import FetchError
from wikihose.reading import MIN_VIEWS, ReadingSource, SpikeRow, Summary
from wikihose.ui.widgets import PageTable, spark

ROW_STEPS = (25, 50, 100)  # the first is the default (owner's choice)
VIEWS_WIDTH = 10
USUAL_WIDTH = 9
SPIKE_WIDTH = 10
WIDE_FROM = 100  # columns of window width where the sparkline gets one cell per day instead of one per two days
EXTRACT_LIMIT = 260  # what fits the 4 spare lines of the detail pane at 80 columns
Factory = Callable[[str], ReadingSource | None]


def spike_text(row: SpikeRow) -> str:
    """``1,234x``; a tilde marks a rough figure (the page's own history was not used)."""
    return f"{'' if row.exact else '~'}{row.multiple:,.0f}x"


def usual_text(row: SpikeRow) -> str:
    return f"{'' if row.exact else '~'}{row.baseline:,.0f}"


def describe(error: FetchError) -> str:
    if error.kind == "rate_limited":
        return f"Pageviews: rate limited, retrying in {int(error.retry_after or 5)}s"
    if error.kind == "down":
        return "Pageviews: could not reach Wikimedia. Press r to try again."
    return "Pageviews: Wikimedia sent something unexpected. Press r to try again."


def columns(width: int) -> tuple[int, int]:
    """(article width, sparkline cells) for a tab ``width`` cells wide. Nothing scrolls sideways."""
    cells = 30 if width >= WIDE_FROM else 15
    fixed = VIEWS_WIDTH + USUAL_WIDTH + SPIKE_WIDTH + cells
    return max(width - fixed - 2 * 5 - 2, 10), cells  # padding of each of the five columns, and the scrollbar


class ReadingTab(Vertical):
    BINDINGS = [
        Binding("r", "refresh", "Refresh", tooltip="Fetch the reading numbers again"),
        Binding("n", "more_rows", "Rows", tooltip="Show 25, 50 or 100 rows"),
    ]

    DEFAULT_CSS = """
    ReadingTab { height: 1fr; }
    ReadingTab #reading-status { height: 1; padding: 0 1; color: $text-muted; }
    ReadingTab #reading-table { height: 1fr; width: 100%; overflow-x: hidden; }
    ReadingTab #reading-detail { height: 7; padding: 0 1; border-top: solid $primary 40%; overflow-y: hidden; }
    """

    class Loaded(Message):
        """A ranking for ``wiki`` finished loading (it may be empty), or failed with ``error``. Convergence listens."""

        def __init__(self, wiki: str, rows: list[SpikeRow], error: str = "") -> None:
            super().__init__()
            self.wiki = wiki
            self.rows = rows
            self.error = error

    def __init__(self, factory: Factory, wiki: str) -> None:
        super().__init__(id="reading-tab")
        self._factory = factory
        self.wiki = wiki
        self._source: ReadingSource | None = factory(wiki)
        self._rows: list[SpikeRow] = []
        self._by_title: dict[str, SpikeRow] = {}
        self._limit = ROW_STEPS[0]
        self._started = False
        self._message = ""  # a loading, error or replay message; empty means show the usual one-line description
        self._summaries: dict[str, Summary | None] = {}
        self._retry: Timer | None = None

    # --- layout ------------------------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="reading-status")
        yield PageTable(self._page_of_row, id="reading-table", cursor_type="row", zebra_stripes=False)
        yield Static(id="reading-detail")

    def on_mount(self) -> None:
        self._draw_status()
        self._show_detail(None)

    def on_show(self) -> None:
        """Load the first time the tab is opened, so the Live tab's start-up does not wait on 80 requests."""
        self.start()

    def start(self) -> None:
        """Load once: when this tab or the Convergence tab is first opened."""
        if not self._started:
            self._started = True
            self.load()

    @property
    def source(self) -> ReadingSource | None:
        return self._source

    def on_resize(self, event: events.Resize) -> None:
        self._draw()

    # --- loading -----------------------------------------------------------------------------------------------------

    def load(self) -> None:
        self._stop_retry()
        if self._source is None:
            self._rows = []
            self._draw()
            self._say("Reading needs a live connection; it is not available in a replay or for this wiki.")
            return
        # Textual types a worker as returning Never; passing partial() keeps the coroutine unstarted if it is cancelled
        job = partial(self._load, self._source)
        self.run_worker(job, name="reading", group="reading", exclusive=True)  # type: ignore[arg-type]

    async def _load(self, source: ReadingSource) -> None:
        self._say("Loading the daily lists…")
        try:
            rows = await source.load(lambda done, total: self._say(f"Loading… {done}/{total}"))
        except FetchError as error:
            if source is not self._source:
                return
            self._say(describe(error))
            self.post_message(self.Loaded(self.wiki, [], describe(error)))
            if error.kind == "rate_limited":
                self._retry = self.set_timer(max(error.retry_after or 5, 1), self.load)
            return
        if source is not self._source:  # the wiki changed while this was loading
            return
        self._rows = rows
        self._draw()
        self._say("")
        self.post_message(self.Loaded(self.wiki, rows))

    def _stop_retry(self) -> None:
        if self._retry is not None:
            self._retry.stop()
            self._retry = None

    def set_wiki(self, wiki: str) -> None:
        """Follow the Live tab to another wiki: forget the rows and, if the tab was opened, load the new ones."""
        if wiki == self.wiki:
            return
        self.wiki = wiki
        self._source = self._factory(wiki)
        self._stop_retry()
        self._message = ""
        self._rows = []
        self._summaries.clear()
        self._draw()
        if self._started:
            self.load()

    def action_refresh(self) -> None:
        self.load()

    def action_more_rows(self) -> None:
        self._limit = ROW_STEPS[(ROW_STEPS.index(self._limit) + 1) % len(ROW_STEPS)]
        self._draw()

    # --- the table ---------------------------------------------------------------------------------------------------

    def _page_of_row(self, key: str) -> tuple[str, str] | None:
        return (self.wiki, key) if key in self._by_title else None

    def _say(self, text: str) -> None:
        self._message = text
        self._draw_status()

    def _draw_status(self) -> None:
        text = self._message
        if not text:
            day = self._source.day if self._source is not None else None
            when = f"{day:%d %b}".lstrip("0") if isinstance(day, date) else "no day yet"
            if self._rows:
                shown = min(self._limit, len(self._rows))
                text = (
                    f"{when} (UTC) · today ÷ 30-day median · {MIN_VIEWS:,}+ views · "
                    f"{shown} of {len(self._rows)} · ~ is rough"
                )
            else:
                text = f"No spikes found for {when}. Press r to try again."
        self.query_one("#reading-status", Static).update(Text(text, no_wrap=True, overflow="ellipsis"))

    def _draw(self) -> None:
        width = self.size.width
        if width <= 0:
            return
        article, cells = columns(width)
        table = self.query_one("#reading-table", PageTable)
        keep = table.cursor_key()
        scroll = table.scroll_y
        table.clear(columns=True)
        table.add_column("Article", width=article, key="Article")
        table.add_column(Text("Views", justify="right"), width=VIEWS_WIDTH, key="Views")
        table.add_column(Text("Usual", justify="right"), width=USUAL_WIDTH, key="Usual")
        table.add_column(Text("Spike", justify="right"), width=SPIKE_WIDTH, key="Spike")
        table.add_column("30 days", width=cells, key="30 days")
        shown = self._rows[: self._limit]
        self._by_title = {row.title: row for row in shown}
        theme = self.app.current_theme
        for row in shown:
            title = Text(row.display_title, no_wrap=True)
            title.truncate(article, overflow="ellipsis")
            table.add_row(
                title,
                Text(f"{row.views:,}", justify="right"),
                Text(usual_text(row), justify="right", style="dim"),
                Text(spike_text(row), justify="right", style=f"bold {theme.primary}"),
                Text(spark(row.series, cells) or "·", style=theme.primary if row.exact else "dim"),
                key=row.title,
            )
        if keep in self._by_title:
            table.move_cursor(row=table.get_row_index(keep), animate=False, scroll=False)
            self.call_after_refresh(table.scroll_to, y=scroll, animate=False, immediate=True)
        self._draw_status()
        self._show_detail(table.cursor_key())

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._show_detail(event.row_key.value)

    # --- the detail pane ---------------------------------------------------------------------------------------------

    def _show_detail(self, title: str | None) -> None:
        row = self._by_title.get(title) if title else None
        if row is None:
            self.query_one("#reading-detail", Static).update(
                Text("Highlight an article to read its summary.", style="dim")
            )
            return
        if row.title in self._summaries:
            self._render_detail(row, self._summaries[row.title], loading=False)
            return
        self._render_detail(row, None, loading=True)
        if self._source is not None:
            job = partial(self._fetch_summary, self._source, row)
            self.run_worker(job, name="summary", group="summary", exclusive=True)  # type: ignore[arg-type]

    async def _fetch_summary(self, source: ReadingSource, row: SpikeRow) -> None:
        try:
            summary = await source.summary(row.title)
        except FetchError:
            summary = None
        if source is not self._source:
            return
        if summary is not None:  # a failure is not remembered, so moving back onto the row tries again
            self._summaries[row.title] = summary
        if self.query_one("#reading-table", PageTable).cursor_key() == row.title:
            self._render_detail(row, summary, loading=False)

    def _render_detail(self, row: SpikeRow, summary: Summary | None, *, loading: bool) -> None:
        text = Text()
        text.append(row.display_title, style="bold")
        if summary is not None and summary.description:
            text.append(f" · {summary.description}", style="dim")
        text.append("\n")
        text.append(
            f"{row.views:,} views · usual {usual_text(row)} · {spike_text(row)}", style=self.app.current_theme.primary
        )
        text.append("\n")
        if loading:
            text.append("Loading the summary…", style="dim")
        elif summary is not None:
            extract = summary.extract
            text.append(extract if len(extract) <= EXTRACT_LIMIT else extract[: EXTRACT_LIMIT - 1].rstrip() + "…")
        else:
            text.append("No summary available.", style="dim")
        self.query_one("#reading-detail", Static).update(text)
