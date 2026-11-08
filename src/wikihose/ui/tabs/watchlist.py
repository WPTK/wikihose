"""The Watchlist tab: the pages you track, how many edits you have not looked at, and keys to open, mark or remove one.

Enter opens the Diff tab from the revision last seen to the newest known. ``m`` marks the page seen without opening it
and ``d`` (or Delete) stops watching it after a question. Adding a page is the shell's ``w`` and ``W``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import tzinfo
from functools import partial

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.timer import Timer
from textual.widgets import DataTable, Static

from wikihose.diff import DiffHeader
from wikihose.net import FetchError
from wikihose.store import WatchedPage
from wikihose.titles import article_url, host_for_wiki
from wikihose.trend import Trends
from wikihose.ui.dialogs import ConfirmRemove
from wikihose.ui.tabs.diff import DiffRequest, OpenDiff
from wikihose.ui.tabs.reading import describe
from wikihose.ui.widgets import spark
from wikihose.watchlist import Watchlist

EDITOR_FROM = 100  # columns of window width
EDITOR_WIDTH = 18
TREND_WIDTH = 8  # one cell per three or four days
WIKI_WIDTH = 8
UNSEEN_WIDTH = 6
LAST_WIDTH = 12
STATUS_WIDTH = 8
EMPTY = (
    "You are not watching any pages yet.\n\n"
    "On the Live tab, move to an edit and press w to watch its page. "
    "Press W anywhere to type a page title or paste a Wikipedia link."
)


def page_key_of(page: WatchedPage) -> str:
    return f"{page.wiki}:{page.page_id}"


class WatchlistTab(Vertical):
    BINDINGS = [
        Binding("m", "mark_seen", "Seen", tooltip="Mark the selected page as seen"),
        Binding("d", "remove", "Remove", tooltip="Stop watching the selected page (asks first)"),
        Binding("delete", "remove", "Remove", show=False, tooltip="Stop watching the selected page (asks first)"),
    ]

    DEFAULT_CSS = """
    WatchlistTab { height: 1fr; }
    WatchlistTab DataTable { height: 1fr; }
    WatchlistTab #watch-status { display: none; height: 1; padding: 0 1; color: $text-muted; }
    WatchlistTab #watch-empty { padding: 1 2; color: $text-muted; height: 1fr; }
    """

    def __init__(
        self, watchlist: Watchlist, tz: tzinfo, editor_of: Callable[[WatchedPage], str], trends: Trends | None = None
    ) -> None:
        super().__init__(id="watchlist-tab")
        self._watchlist = watchlist
        self._tz = tz
        self._editor_of = editor_of
        self._rows: dict[str, WatchedPage] = {}
        self._pending = False  # a redraw is already queued
        self._layout: tuple[bool, int] | None = None  # (wide, title width) the columns were built for
        self._trends = trends
        self._series: dict[str, tuple[int, ...] | None] = {}  # page key -> 30 days of views; None: no data
        self._filling = False  # the trend worker is running, so a page without a trend yet shows "·"
        self._shown = False
        self._retry: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="watch-status")
        yield Static(EMPTY, id="watch-empty")
        yield DataTable(id="watch-table", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self._watchlist.subscribe(self._schedule_refresh)
        self.refresh_rows()

    def on_resize(self, event: events.Resize) -> None:
        was_wide = self._layout is not None and self._layout[0]
        self.refresh_rows()
        if self._layout is not None and self._layout[0] and not was_wide:
            self._start_fill()

    def on_show(self) -> None:
        self._shown = True
        self._start_fill()

    def on_hide(self) -> None:
        self._shown = False

    # --- the trend column --------------------------------------------------------------------------------------------

    def _say(self, text: str) -> None:
        status = self.query_one("#watch-status", Static)
        status.display = bool(text)
        status.update(Text(text, no_wrap=True, overflow="ellipsis"))

    def _start_fill(self) -> None:
        """Fetch the missing trends, one page at a time, when the column is on screen. A restart skips what is done."""
        if self._retry is not None:
            self._retry.stop()
            self._retry = None
        if self._trends is None or not self._shown or not self._layout or not self._layout[0]:
            return
        if all(page_key_of(page) in self._series for page in self._watchlist.pages()):
            return
        job = partial(self._fill, self._trends)
        self.run_worker(job, name="trend", group="trend", exclusive=True)  # type: ignore[arg-type]

    async def _fill(self, trends: Trends) -> None:
        self._filling = True
        self.refresh_rows()
        try:
            first = True
            for page in self._watchlist.pages():
                key = page_key_of(page)
                if key in self._series:
                    continue
                if not trends.supported(page):
                    self._series[key] = None
                    continue
                if not first:
                    await trends.pause()
                first = False
                try:
                    self._series[key] = await trends.fetch(page)
                except FetchError as error:
                    self._say(describe(error))
                    if error.kind == "bad_data":  # one odd page must not block the rest
                        self._series[key] = None
                        continue
                    if error.kind == "rate_limited":
                        self._retry = self.set_timer(max(error.retry_after or 5, 1), self._start_fill)
                    return
                self._schedule_refresh()
            self._say("")
        finally:
            self._filling = False
            self.refresh_rows()

    def _trend_cell(self, page: WatchedPage) -> Text:
        key = page_key_of(page)
        if key not in self._series:
            return Text("·" if self._filling else "-", style="dim")
        series = self._series[key]
        if not series or not any(series):
            return Text("-", style="dim")
        return Text(spark(series, TREND_WIDTH), style=self.app.current_theme.primary, no_wrap=True)

    # --- drawing -----------------------------------------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        """Many changes in a row (a catch-up page by page) draw the table once."""
        if self._pending or not self.is_mounted:
            return
        self._pending = True
        self.call_later(self._refresh_now)

    def _refresh_now(self) -> None:
        self._pending = False
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if not self.is_mounted:
            return
        table = self.query_one("#watch-table", DataTable)
        pages = self._watchlist.pages()
        self.query_one("#watch-empty", Static).display = not pages
        table.display = bool(pages)
        keep = self.page_under_cursor()
        keep_key = None if keep is None else page_key_of(keep)
        scroll = table.scroll_y
        wide = self.size.width >= EDITOR_FROM
        fixed = WIKI_WIDTH + UNSEEN_WIDTH + LAST_WIDTH + STATUS_WIDTH + (EDITOR_WIDTH + TREND_WIDTH if wide else 0)
        title_width = max(self.size.width - fixed - 2 * (5 + 2 * wide) - 2, 12)
        if self._layout == (wide, title_width):
            table.clear()  # same columns: only the rows change
        else:
            table.clear(columns=True)
            table.add_column("Wiki", width=WIKI_WIDTH)
            table.add_column("Page", width=title_width)
            table.add_column("New", width=UNSEEN_WIDTH)
            table.add_column("Last edit", width=LAST_WIDTH)
            if wide:
                table.add_column("Editor", width=EDITOR_WIDTH)
                table.add_column("Trend", width=TREND_WIDTH)
            table.add_column("Status", width=STATUS_WIDTH)
            self._layout = (wide, title_width)
        self._rows = {}
        theme = self.app.current_theme
        for page in pages:
            key = page_key_of(page)
            self._rows[key] = page
            title = Text(page.title, no_wrap=True, style="bold" if page.unseen else "")
            title.truncate(title_width, overflow="ellipsis")
            unseen = Text(
                str(page.unseen) if page.unseen else "·", justify="right", style="bold" if page.unseen else "dim"
            )
            if page.unseen and theme.accent:
                unseen.stylize(theme.accent)
            when = "-" if page.last_edit_at is None else page.last_edit_at.astimezone(self._tz).strftime("%d %b %H:%M")
            cells = [Text(page.wiki, style="dim", no_wrap=True), title, unseen, Text(when, style="dim")]
            if wide:
                editor = Text(self._editor_of(page) or "-", style="dim", no_wrap=True)
                editor.truncate(EDITOR_WIDTH, overflow="ellipsis")
                cells += [editor, self._trend_cell(page)]
            cells.append(Text("deleted", style=theme.error or "bold") if page.deleted else Text(""))
            table.add_row(*cells, key=key)
        if keep_key in self._rows:
            table.move_cursor(row=table.get_row_index(keep_key), animate=False, scroll=False)
            if scroll:
                self.call_after_refresh(table.scroll_to, y=scroll, animate=False, immediate=True)

    def page_under_cursor(self) -> WatchedPage | None:
        table = self.query_one("#watch-table", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return None if key is None else self._rows.get(key)

    # --- keys --------------------------------------------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        page = self._rows.get(event.row_key.value or "")
        if page is None:
            return
        if page.seen_rev_id <= 0 or page.last_rev_id <= page.seen_rev_id:
            self.app.notify(f"Nothing new on {page.title} since you last looked.", timeout=4, markup=False)
            return
        # One new edit has one editor; several do not, and the last editor alone would read as the author of all.
        user = self._editor_of(page) if page.unseen == 1 else ""
        header = DiffHeader(
            title=page.title,
            user=user,
            at=page.last_edit_at,
            url=article_url(host_for_wiki(page.wiki) or "", page.title),
        )
        self.post_message(OpenDiff(DiffRequest(page.wiki, page.seen_rev_id, page.last_rev_id, header)))

    def action_mark_seen(self) -> None:
        page = self.page_under_cursor()
        if page is None:
            return
        self._watchlist.mark_seen(page)
        self.app.notify(f"Marked {page.title} as seen.", timeout=3, markup=False)

    def action_remove(self) -> None:
        page = self.page_under_cursor()
        if page is not None:
            self.app.push_screen(ConfirmRemove(page.title), lambda yes: self._remove(page, yes))

    def _remove(self, page: WatchedPage, yes: bool | None) -> None:
        if yes and self._watchlist.remove(page):
            self.app.notify(f"Stopped watching {page.title}.", timeout=3, markup=False)
