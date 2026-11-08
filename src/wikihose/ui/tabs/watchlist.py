"""The Watchlist tab: the pages you track, how many edits you have not looked at, and keys to open, mark or remove one.

Enter opens the Diff tab from the revision last seen to the newest known. ``m`` marks the page seen without opening it
and ``d`` (or Delete) stops watching it after a question. Adding a page is the shell's ``w`` and ``W``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import tzinfo

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from wikihose.diff import DiffHeader
from wikihose.store import WatchedPage
from wikihose.titles import article_url, host_for_wiki
from wikihose.ui.dialogs import ConfirmRemove
from wikihose.ui.tabs.diff import DiffRequest, OpenDiff
from wikihose.watchlist import Watchlist

EDITOR_FROM = 100  # columns of window width
EDITOR_WIDTH = 18
TREND_WIDTH = 8  # room for the sparkline that is wired in later
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
    WatchlistTab #watch-empty { padding: 1 2; color: $text-muted; height: 1fr; }
    """

    def __init__(self, watchlist: Watchlist, tz: tzinfo, editor_of: Callable[[WatchedPage], str]) -> None:
        super().__init__(id="watchlist-tab")
        self._watchlist = watchlist
        self._tz = tz
        self._editor_of = editor_of
        self._rows: dict[str, WatchedPage] = {}
        self._pending = False  # a redraw is already queued
        self._layout: tuple[bool, int] | None = None  # (wide, title width) the columns were built for

    def compose(self) -> ComposeResult:
        yield Static(EMPTY, id="watch-empty")
        yield DataTable(id="watch-table", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self._watchlist.subscribe(self._schedule_refresh)
        self.refresh_rows()

    def on_resize(self, event: events.Resize) -> None:
        self.refresh_rows()

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
                cells += [editor, Text("-", style="dim")]
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
