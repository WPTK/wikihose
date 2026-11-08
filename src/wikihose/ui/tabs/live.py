"""The Live tab: a scrolling edit log, edits per minute, the most edited articles and the people/bots split."""

from __future__ import annotations

import itertools
from collections import deque
from collections.abc import Callable
from datetime import tzinfo

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import DataTable, Sparkline, Static

from wikihose.clock import Clock
from wikihose.config import Config
from wikihose.live_stats import LiveStats
from wikihose.sources.events import Edit, StreamItem
from wikihose.store import Store
from wikihose.titles import page_key
from wikihose.ui.dialogs import EditDetail, WikiPrompt
from wikihose.ui.widgets import PageTable
from wikihose.watchlist import Watchlist

SIDE_WIDTH = 34
SIDE_INNER = SIDE_WIDTH - 3  # the left border and one cell of padding on each side
LOG_LIMIT = 500
TOP_COUNT = 30
TOP_EDITS_WIDTH = 5
TOP_TITLE_WIDTH = SIDE_INNER - 2 - (TOP_EDITS_WIDTH + 2) - 2  # scrollbar, the Edits column and its own padding
MIN_BYTES_STEPS = (0, 100, 1000)
EDITOR_FROM = 105  # columns of window width
SUMMARY_FROM = 145
TIME_WIDTH = 8
BYTES_WIDTH = 7
EDITOR_WIDTH = 18
FLUSH_SECONDS = 0.25
SIDE_SECONDS = 2.0


def format_delta(delta: int) -> str:
    """``+1,234`` or ``-56``; shortened to ``+123k`` when it would not fit the seven-cell column."""
    text = f"{delta:+,}"
    return text if len(text) <= BYTES_WIDTH else f"{delta / 1000:+.0f}k"


def size_words(min_bytes: int) -> str:
    return "any size" if min_bytes <= 0 else f"{min_bytes:,}+ bytes"


def log_columns(width: int) -> list[tuple[str, int]]:
    """The log's columns for a tab ``width`` cells wide: (label, content width). Nothing scrolls sideways."""
    available = width - SIDE_WIDTH - 2  # the log's own scrollbar
    labels = ["Time", "Bytes", "Article"]
    fixed = TIME_WIDTH + BYTES_WIDTH
    if width >= EDITOR_FROM:
        labels.append("Editor")
        fixed += EDITOR_WIDTH
    if width >= SUMMARY_FROM:
        labels.append("Summary")
    rest = max(available - fixed - 2 * len(labels), 8)
    if "Summary" in labels:
        article = min(rest - 12, max(40, rest // 3))
        widths = {"Article": article, "Summary": rest - article}
    else:
        widths = {"Article": rest}
    widths.update({"Time": TIME_WIDTH, "Bytes": BYTES_WIDTH, "Editor": EDITOR_WIDTH})
    return [(label, widths[label]) for label in labels]


class FilterLine(Static):
    """The one-line state of the filters. Each word is a click target that does what its key does."""

    class Picked(Message):
        def __init__(self, what: str) -> None:
            super().__init__()
            self.what = what

    DEFAULT_CSS = """
    FilterLine { height: 1; padding: 0 1; color: $text-muted; }
    """

    def action_pick(self, what: str) -> None:
        self.post_message(self.Picked(what))


class LiveTab(Vertical):
    class FiltersChanged(Message):
        """A filter changed; the shell saves it to the config file."""

        def __init__(self, hide_bots: bool, articles_only: bool, min_bytes: int) -> None:
            super().__init__()
            self.hide_bots = hide_bots
            self.articles_only = articles_only
            self.min_bytes = min_bytes

    class WikiChanged(Message):
        def __init__(self, wiki: str) -> None:
            super().__init__()
            self.wiki = wiki

    BINDINGS = [
        Binding("b", "toggle_bots", "Bots", tooltip="Hide or show bot edits"),
        Binding("a", "toggle_articles", "Articles", tooltip="Show only articles, or every kind of page"),
        Binding("m", "cycle_size", "Size", tooltip="Cycle the smallest change shown: any, 100, 1,000 bytes"),
        Binding("i", "change_wiki", "Wiki", tooltip="Type another wiki to watch, such as dewiki"),
        Binding("p", "toggle_pause", "Pause", tooltip="Pause or resume the log (the numbers keep counting)"),
    ]

    DEFAULT_CSS = """
    LiveTab { height: 1fr; }
    LiveTab #live-body { height: 1fr; }
    LiveTab #log { width: 1fr; height: 1fr; overflow-x: hidden; }
    LiveTab #side { width: 34; height: 1fr; border-left: solid $primary 40%; padding: 0 1; }
    LiveTab #side .heading { color: $primary; text-style: bold; height: 1; }
    LiveTab #spark { height: 3; }
    LiveTab #split { height: 2; }
    LiveTab #top { height: 1fr; width: 100%; overflow-x: hidden; }
    LiveTab Sparkline > .sparkline--max-color { color: $primary; }
    LiveTab Sparkline > .sparkline--min-color { color: $primary 50%; }
    """

    def __init__(
        self,
        config: Config,
        clock: Clock,
        tz: tzinfo,
        store: Store | None,
        watchlist: Watchlist,
        subscribe: Callable[[Callable[[StreamItem], None]], None],
    ) -> None:
        super().__init__(id="live-tab")
        self.wiki = config.wiki
        self.hide_bots = config.hide_bots
        self.articles_only = config.articles_only
        self.min_bytes = config.min_bytes
        self.paused = False
        self._clock = clock
        self._tz = tz
        self._store = store
        self._watchlist = watchlist
        self._subscribe = subscribe
        self._stats = LiveStats(self.wiki, clock)
        self._seq = itertools.count(1)
        self._ring: deque[tuple[str, Edit]] = deque(maxlen=LOG_LIMIT)  # every parsed edit of this wiki, newest last
        self._pending: deque[tuple[str, Edit]] = deque(maxlen=LOG_LIMIT)  # passed the filters, not yet in the log
        self._rows: deque[tuple[str, Edit]] = deque(maxlen=LOG_LIMIT)  # in the log, newest first
        self._edits: dict[str, Edit] = {}  # row key -> edit, for the rows in the log
        self._cells: dict[str, list[Text]] = {}
        self._paused_at: str | None = None  # the newest key when the log was paused
        self._columns: list[tuple[str, int]] = []
        self._top_keys: list[str] = []

    # --- layout ------------------------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield FilterLine(id="filters")
        with Horizontal(id="live-body"):
            yield PageTable(self._page_of_row, id="log", cursor_type="row", zebra_stripes=False)
            with Vertical(id="side"):
                yield Static(id="epm", classes="heading")
                yield Sparkline([0] * 60, id="spark")
                yield Static(id="split")
                yield Static("Most edited, last 15 min", classes="heading")
                yield PageTable(self._page_of_top, id="top", cursor_type="row", cell_padding=1)

    def on_mount(self) -> None:
        self._subscribe(self.accept)
        self._watchlist.subscribe(self._watchlist_changed)
        top = self.query_one("#top", PageTable)
        top.add_column("Edits", width=TOP_EDITS_WIDTH)
        top.add_column("Article", width=TOP_TITLE_WIDTH)
        self._draw_filters()
        self.refresh_side()
        self.set_interval(FLUSH_SECONDS, self.flush_log)
        self.set_interval(SIDE_SECONDS, self.refresh_side)
        self.call_after_refresh(self._relayout)
        self.query_one("#log").focus()

    def on_resize(self, event: events.Resize) -> None:
        self._relayout()

    def _relayout(self) -> None:
        width = self.size.width
        if width <= 0:
            return
        columns = log_columns(width)
        if columns != self._columns:
            self._columns = columns
            self._cells.clear()
            self._draw_log()

    # --- incoming edits ----------------------------------------------------------------------------------------------

    def accept(self, item: StreamItem) -> None:
        """Take one item from the stream: count the edits of the selected wiki and queue the ones the filters allow."""
        if not isinstance(item, Edit) or item.wiki != self.wiki:
            return
        self._stats.add(item)
        entry = (f"e{next(self._seq)}", item)
        self._ring.append(entry)
        if self._passes(item):
            self._pending.append(entry)

    def _passes(self, edit: Edit) -> bool:
        if self.hide_bots and edit.bot:
            return False
        if self.articles_only and edit.namespace != 0:
            return False
        return abs(edit.delta) >= self.min_bytes

    def flush_log(self) -> None:
        """Move queued edits into the log. Runs every quarter second, so a burst makes one redraw. Does nothing paused."""
        if self.paused or not self._pending:
            return
        while self._pending:
            self._rows.appendleft(self._pending.popleft())
        self._draw_log()

    def _refill(self) -> None:
        """Rebuild the log from the ring after a filter changed."""
        limit = self._paused_at if self.paused else None
        self._rows.clear()
        self._pending.clear()
        for key, edit in reversed(self._ring):
            if limit is not None and int(key[1:]) > int(limit[1:]):
                continue
            if self._passes(edit):
                self._rows.append((key, edit))
        self._draw_log()

    # --- the log -----------------------------------------------------------------------------------------------------

    def _page_of_row(self, key: str) -> tuple[str, str] | None:
        edit = self._edits.get(key)
        return None if edit is None else (edit.wiki, edit.title)

    def _draw_log(self) -> None:
        if not self._columns:
            return
        table = self.query_one("#log", PageTable)
        keep = table.cursor_key()
        at_top = table.cursor_row == 0
        table.clear(columns=True)
        for label, width in self._columns:
            table.add_column(label, width=width, key=label)
        self._edits = {}
        for key, edit in self._rows:
            self._edits[key] = edit
            table.add_row(*self._row_cells(key, edit), key=key)
        if keep in self._edits and not at_top:
            table.move_cursor(row=table.get_row_index(keep), animate=False)

    def _row_cells(self, key: str, edit: Edit) -> list[Text]:
        cells = self._cells.get(key)
        if cells is None:
            cells = self._build_cells(edit)
            self._cells[key] = cells
            if len(self._cells) > 2 * LOG_LIMIT:  # forget rows that scrolled out of the log
                for old in [k for k in self._cells if k not in self._edits and k != key]:
                    del self._cells[old]
        return cells

    def _build_cells(self, edit: Edit) -> list[Text]:
        theme = self.app.current_theme
        widths = dict(self._columns)
        dim = "dim" if edit.bot else ""
        if edit.delta > 0:
            bytes_style = f"{theme.success} {dim}".strip()
        elif edit.delta < 0:
            bytes_style = f"{theme.error} {dim}".strip()
        else:
            bytes_style = "dim"
        watched = (edit.wiki, edit.title) in self._watchlist
        title = Text(no_wrap=True)
        if edit.bot:
            title.append("bot ", style="dim")
        if edit.kind == "new":
            title.append("new ", style="bold")
        title.append(edit.title, style=theme.accent if watched else dim)
        title.truncate(widths["Article"], overflow="ellipsis")
        cells = [
            Text(edit.at.astimezone(self._tz).strftime("%H:%M:%S"), style="dim"),
            Text(format_delta(edit.delta), style=bytes_style, justify="right"),
            title,
        ]
        if "Editor" in widths:
            editor = Text(edit.user, style=dim, no_wrap=True)
            editor.truncate(widths["Editor"], overflow="ellipsis")
            cells.append(editor)
        if "Summary" in widths:
            summary = Text(edit.comment, style="dim", no_wrap=True)
            summary.truncate(widths["Summary"], overflow="ellipsis")
            cells.append(summary)
        return cells

    def _watchlist_changed(self) -> None:
        self._cells.clear()
        self._draw_log()
        self.refresh_side()

    # --- the side column ---------------------------------------------------------------------------------------------

    def _page_of_top(self, key: str) -> tuple[str, str] | None:
        return (self.wiki, key) if key in self._top_keys else None

    def refresh_side(self) -> None:
        """Redraw the numbers on the right and give the store its once-a-minute write."""
        stats = self._stats
        stats.maybe_flush(self._store)
        self.query_one("#epm", Static).update(Content.assemble("Edits per minute  ", (str(stats.per_minute()), "bold")))
        self.query_one("#spark", Sparkline).data = [float(n) for n in stats.series()]
        people, bots = stats.split()
        self.query_one("#split", Static).update(self._split_content(people, bots))
        self._draw_top()

    @staticmethod
    def _split_content(people: int, bots: int) -> Content:
        total = people + bots
        if total == 0:
            return Content.assemble(("░" * SIDE_INNER, "$primary 40%"), "\nNo edits yet")
        share = round(100 * people / total)
        filled = round(SIDE_INNER * people / total)
        return Content.assemble(
            ("█" * filled, "$success"),
            ("█" * (SIDE_INNER - filled), "$primary"),
            "\n",
            (f"People {share}%", "$success"),
            "   ",
            (f"Bots {100 - share}%", "$primary"),
        )

    def _draw_top(self) -> None:
        theme = self.app.current_theme
        table = self.query_one("#top", PageTable)
        keep = table.cursor_key()
        at_top = table.cursor_row == 0
        table.clear()
        self._top_keys = []
        for title, count in self._stats.top(TOP_COUNT):
            shown = Text(title, no_wrap=True, style=theme.accent if (self.wiki, title) in self._watchlist else "")
            shown.truncate(TOP_TITLE_WIDTH, overflow="ellipsis")
            table.add_row(Text(f"{count}", justify="right"), shown, key=title)
            self._top_keys.append(title)
        if keep in self._top_keys and not at_top:
            table.move_cursor(row=table.get_row_index(keep), animate=False)

    # --- the filter line and its keys --------------------------------------------------------------------------------

    def _draw_filters(self) -> None:
        def word(what: str, text: str) -> str:
            return f"[$primary @click=pick('{what}')]{text}[/]"

        parts = [
            word("wiki", self.wiki),
            word("bots", "bots hidden" if self.hide_bots else "bots shown"),
            word("articles", "articles only" if self.articles_only else "all pages"),
            word("size", size_words(self.min_bytes)),
            word("pause", "paused" if self.paused else "live"),
        ]
        self.query_one("#filters", FilterLine).update(" · ".join(parts))

    def on_filter_line_picked(self, event: FilterLine.Picked) -> None:
        event.stop()
        actions = {
            "wiki": self.action_change_wiki,
            "bots": self.action_toggle_bots,
            "articles": self.action_toggle_articles,
            "size": self.action_cycle_size,
            "pause": self.action_toggle_pause,
        }
        actions[event.what]()

    def _filters_changed(self) -> None:
        self._draw_filters()
        self._refill()
        self.post_message(self.FiltersChanged(self.hide_bots, self.articles_only, self.min_bytes))

    def action_toggle_bots(self) -> None:
        self.hide_bots = not self.hide_bots
        self._filters_changed()

    def action_toggle_articles(self) -> None:
        self.articles_only = not self.articles_only
        self._filters_changed()

    def action_cycle_size(self) -> None:
        steps = MIN_BYTES_STEPS
        index = steps.index(self.min_bytes) if self.min_bytes in steps else -1
        self.min_bytes = steps[(index + 1) % len(steps)]
        self._filters_changed()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self._paused_at = self._ring[-1][0] if self._ring else "e0"
        else:
            self._paused_at = None
            self._refill()  # adds what arrived while paused
        self._draw_filters()

    def action_change_wiki(self) -> None:
        self.app.push_screen(WikiPrompt(), self.set_wiki)

    def set_wiki(self, wiki: str | None) -> None:
        """Switch to another wiki. Its numbers start from zero; the old wiki's counts go to the store first."""
        if not wiki or wiki == self.wiki:
            return
        self._stats.flush(self._store)
        self.wiki = wiki
        self._stats = LiveStats(wiki, self._clock)
        self._ring.clear()
        self._pending.clear()
        self._rows.clear()
        self._cells.clear()
        self._paused_at = "e0" if self.paused else None
        self._draw_filters()
        self._draw_log()
        self.refresh_side()
        self.post_message(self.WikiChanged(wiki))

    # --- opening a row -----------------------------------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        key = event.row_key.value
        if key is None:
            return
        if event.data_table.id == "log":
            edit = self._edits.get(key)
            if edit is not None:
                self.app.push_screen(EditDetail(edit.wiki, page_key(edit.wiki, edit.title)[1], edit, self._tz))
        elif event.data_table.id == "top":
            self.app.push_screen(EditDetail(self.wiki, key, self._stats.latest(key), self._tz))

    # --- for tests and the shell -------------------------------------------------------------------------------------

    @property
    def stats(self) -> LiveStats:
        return self._stats

    def row_titles(self) -> list[str]:
        """Titles in the log, newest first."""
        return [edit.title for _, edit in self._rows]
