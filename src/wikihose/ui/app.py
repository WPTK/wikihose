"""The app shell: the top bar, the tab strip, the feed-health bar, the footer and the "make the window bigger" cover.

The shell does not know what is inside a tab. Tabs get the stream by subscribing to it; they tell the shell about
changes with messages. ``w`` asks the focused widget for its page (a ``PageTable``) and otherwise opens a dialog;
``W`` always opens the dialog. The shell owns the watchlist: it matches every live edit and page move against it (from
memory, no requests), runs the startup catch-up in a worker and keeps the Watchlist tab's badge.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Footer, Static, TabbedContent, TabPane
from textual.widgets._tabs import Tab

from wikihose.catchup import CatchUpResult, catch_up
from wikihose.clock import Clock
from wikihose.config import Config, load_config, save_config
from wikihose.net import Client
from wikihose.sources.events import Edit, FeedStatus, PageMove, StreamItem
from wikihose.sources.protocol import DataSource
from wikihose.sources.replay import ReplaySource
from wikihose.store import Store, WatchedPage
from wikihose.titles import page_key
from wikihose.ui.bars import HealthBar, TopBar
from wikihose.ui.bindings import declared_bindings, help_text, key_label
from wikihose.ui.dialogs import FIXED_HELP, AddPage, HelpScreen
from wikihose.ui.tabs.diff import DiffTab, OpenDiff
from wikihose.ui.tabs.live import LiveTab
from wikihose.ui.tabs.stub import STUBS, StubPane
from wikihose.ui.tabs.watchlist import WatchlistTab
from wikihose.ui.theme import THEMES
from wikihose.ui.widgets import PageTable
from wikihose.watchlist import WatchError, Watchlist
from wikihose.watchlist_input import WatchInputError, parse_watch_input

if TYPE_CHECKING:
    from textual.dom import DOMNode

FLOOR = (80, 24)
TABS = ("live", "reading", "watchlist", "diff", "convergence")
REPLAY_FINISHED = "replay finished"
log = logging.getLogger(__name__)


class TooSmall(Static):
    """Covers the whole screen when the window is smaller than the floor."""

    DEFAULT_CSS = """
    TooSmall { layer: top; width: 100%; height: 100%; content-align: center middle; text-align: center;
        background: $background; color: $foreground; display: none; }
    """


class MainScreen(Screen[None]):
    DEFAULT_CSS = """
    MainScreen { layers: default top; }
    MainScreen TabbedContent { height: 1fr; }
    MainScreen TabPane { padding: 0; height: 1fr; }
    """

    AUTO_FOCUS = ""  # each tab puts the focus where it wants it; the tab strip never takes it at start

    def __init__(self, shell: WikihoseApp) -> None:
        super().__init__()
        self.shell = shell
        self._started = False

    def compose(self) -> ComposeResult:
        shell = self.shell
        replay = shell.source if isinstance(shell.source, ReplaySource) else None
        yield TopBar(
            lambda: replay.moment() if replay else shell.clock.now(),
            shell.tz,
            shell.wiki,
            shell.replay_state if replay else None,
        )
        with TabbedContent(initial="live", id="tabs"):
            with TabPane("Live", id="live"):
                yield LiveTab(shell.config, shell.clock, shell.tz, shell.store, shell.watchlist, shell.subscribe)
            with TabPane("Reading", id="reading"):
                yield StubPane(STUBS["reading"][1])
            with TabPane("Watchlist", id="watchlist"):
                yield WatchlistTab(shell.watchlist, shell.tz, shell.last_editor)
            with TabPane("Diff", id="diff"):
                yield DiffTab(shell.client, shell.watchlist, shell.tz)
            with TabPane("Convergence", id="convergence"):
                yield StubPane(STUBS["convergence"][1])
        yield HealthBar()
        yield Footer(show_command_palette=False, compact=True)
        yield TooSmall()

    def on_mount(self) -> None:
        self._check_size(self.size.width, self.size.height)
        self.shell.show_unseen()  # the number saved from last time

    def on_screen_resume(self) -> None:
        """Start the stream once every tab exists and has subscribed to it."""
        if not self._started:
            self._started = True
            self.shell.start_stream()
            self.shell.start_catch_up()

    def on_resize(self, event: events.Resize) -> None:
        self._check_size(event.size.width, event.size.height)

    def _check_size(self, width: int, height: int) -> None:
        cover = self.query_one(TooSmall)
        small = width < FLOOR[0] or height < FLOOR[1]
        if small:
            cover.update(
                f"Make the window bigger.\nwikihose needs {FLOOR[0]}x{FLOOR[1]}; this one is {width}x{height}."
            )
        cover.display = small

    def focus_active_pane(self) -> None:
        """Put the focus on the first thing in the open tab that can take it, so that tab's keys work."""
        tabs = self.query_one(TabbedContent)
        pane = tabs.active_pane
        if pane is None:
            return
        for widget in pane.query("*"):
            if widget.focusable:
                widget.focus()
                return

    def on_click(self, event: events.Click) -> None:
        """After a click on a tab, move the focus into it so its keys work (arrow keys on the strip keep the focus)."""
        if isinstance(event.widget, Tab):
            self.call_after_refresh(self.focus_active_pane)

    def handle_status(self, status: FeedStatus) -> None:
        self.query_one(HealthBar).report(status)

    def set_badge(self, count: int) -> None:
        tab = self.query_one(TabbedContent).get_tab("watchlist")
        tab.label = Content.assemble("Watchlist ", (str(count), "bold $accent")) if count else Content("Watchlist")

    def set_wiki(self, wiki: str) -> None:
        self.query_one(TopBar).set_wiki(wiki)

    def set_note(self, text: str) -> None:
        self.query_one(HealthBar).set_note(text)

    def refresh_top_bar(self) -> None:
        self.query_one(TopBar).refresh_text()


class WikihoseApp(App[None]):
    TITLE = "wikihose"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("1", "tab('live')", "Live", show=False),
        Binding("2", "tab('reading')", "Reading", show=False),
        Binding("3", "tab('watchlist')", "Watchlist", show=False),
        Binding("4", "tab('diff')", "Diff", show=False),
        Binding("5", "tab('convergence')", "Convergence", show=False),
        Binding("w", "watch", "Watch", tooltip="Watch the selected page (W: type a title or link)"),
        Binding("W", "watch_typed", "Type page", show=False, tooltip="Type a page title or paste a link to watch"),
        Binding("question_mark", "help", "Help", key_display="?", tooltip="Show this list of keys"),
        Binding("q", "quit", "Quit", tooltip="Quit wikihose"),
        # Replay keys: only active when the source is a replay (see check_action).
        Binding("space", "replay_pause", "Play", key_display="space", tooltip="Pause or resume the replay"),
        Binding("plus", "replay_faster", "Faster", key_display="+", tooltip="Play the replay faster (up to 16x)"),
        Binding("minus", "replay_slower", "Slower", key_display="-", tooltip="Play the replay slower (down to 0.5x)"),
        Binding(
            "right_square_bracket",
            "replay_skip",
            "Skip",
            key_display="]",
            tooltip="Skip ahead 60 seconds of the replay",
        ),
    ]

    def __init__(
        self,
        source: DataSource,
        config: Config,
        *,
        clock: Clock,
        tz: tzinfo | None = None,
        store: Store | None = None,
        config_path: Path | None = None,
        client: Client | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.config = config
        self.clock = clock
        self.tz: tzinfo = tz or datetime.now().astimezone().tzinfo or UTC
        self.store = store
        self.config_path = config_path  # None: never write the config (--no-config)
        self.wiki = config.wiki
        self.client = client  # None in a replay: no network, so nothing to add and no catch-up
        # Without a database (a replay) the watchlist lives in memory for this run only.
        self._scratch = Store(":memory:") if store is None else None
        self.watchlist = Watchlist(store or self._scratch or Store(":memory:"), client, clock)
        self._editors: dict[tuple[str, int], str] = {}  # last editor seen this run, by (wiki, page ID)
        self._tab = "live"
        self._previous_tab = "live"
        self.replay_finished = False
        self._subscribers: list[Callable[[StreamItem], None]] = []
        self._main = MainScreen(self)
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = config.theme if config.theme in self.available_themes else THEMES[0].name
        self.watchlist.subscribe(self._watchlist_changed)

    # --- screens and data flow ---------------------------------------------------------------------------------------

    def get_default_screen(self) -> Screen[Any]:
        return self._main

    def start_stream(self) -> None:
        self.run_worker(self._pump(), name="stream", group="stream", exclusive=True)

    async def on_unmount(self) -> None:
        await self.source.aclose()
        if self._scratch is not None:
            self._scratch.close()

    def subscribe(self, callback: Callable[[StreamItem], None]) -> None:
        """Tabs call this to receive every item of the stream, in order, on the app's own event loop."""
        self._subscribers.append(callback)

    async def _pump(self) -> None:
        async for item in self.source.items():
            self._dispatch(item)

    def _dispatch(self, item: StreamItem) -> None:
        if isinstance(item, FeedStatus):
            if isinstance(self.source, ReplaySource) and item.state == "down" and item.detail == REPLAY_FINISHED:
                self.replay_finished = True  # the end of a recording is not a failure
                self._main.refresh_top_bar()
            else:
                self._main.handle_status(item)
            return
        if isinstance(item, Edit):
            self._watch_edit(item)
        elif isinstance(item, PageMove):
            self._watch_move(item)
        for callback in list(self._subscribers):
            callback(item)

    # --- tabs --------------------------------------------------------------------------------------------------------

    def action_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id
        self.call_after_refresh(self._main.focus_active_pane)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab = event.pane.id or "live"
        if tab != self._tab:
            self._previous_tab, self._tab = self._tab, tab

    def on_diff_tab_back(self) -> None:
        self.action_tab(self._previous_tab if self._previous_tab != "diff" else "live")

    def on_open_diff(self, message: OpenDiff) -> None:
        self.action_tab("diff")
        self.query_one(DiffTab).show(message.request)

    def _watchlist_changed(self) -> None:
        self.show_unseen()

    def show_unseen(self) -> None:
        self._main.set_badge(self.watchlist.unseen_total())

    # --- live matching: memory only, never a request -----------------------------------------------------------------

    def _watch_edit(self, edit: Edit) -> None:
        page = self.watchlist.record_edit(edit)
        if page is not None:
            self._editors[(page.wiki, page.page_id)] = edit.user

    def _watch_move(self, move: PageMove) -> None:
        page = self.watchlist.apply_move(move.wiki, move.title, move.target)
        if page is not None:
            self.notify(f"{move.title} was moved to {page.title}. Still watching it.", timeout=5, markup=False)

    def last_editor(self, page: WatchedPage) -> str:
        return self._editors.get((page.wiki, page.page_id), "")

    # --- the startup catch-up ----------------------------------------------------------------------------------------

    def start_catch_up(self) -> None:
        """Check the watched pages in the background. Not in a replay, which has no network."""
        if self.client is None or isinstance(self.source, ReplaySource) or not len(self.watchlist):
            return
        self.run_worker(self._catch_up(self.client), name="catch-up", group="catch-up", exclusive=True)

    async def _catch_up(self, client: Client) -> None:
        total = len(self.watchlist)
        what = f"{total} watched page" + ("" if total == 1 else "s")
        self._main.set_note(f"Checking {what}…")
        try:
            result = await catch_up(
                self.watchlist,
                client,
                self.clock,
                lambda done, of: self._main.set_note(f"Checking {what}… {done} of {of}"),
            )
        except Exception:  # a bug here must cost the catch-up, not the app
            log.exception("the startup catch-up failed")
            self.notify("Could not check your watched pages.", severity="warning", timeout=6, markup=False)
            return
        finally:
            self._main.set_note("")
        self._report_catch_up(what, result)

    def _report_catch_up(self, what: str, result: CatchUpResult) -> None:
        for edit in reversed(result.edits):  # newest first within a page, so the last write is the newest
            self._editors[(edit.wiki, edit.page_id)] = edit.user
        changed = len({(edit.wiki, edit.page_id) for edit in result.edits})
        if changed:
            text = f"{changed} of {what} changed while you were away."
        elif result.errors:
            text = ""
        else:
            text = f"Checked {what}. Nothing new."
        if text:
            self.notify(text, timeout=5, markup=False)
        for old, page in result.renamed:
            self.notify(f"{old} is now called {page.title}.", timeout=6, markup=False)
        for page in result.deleted:
            self.notify(
                f"{page.title} was deleted. It stays on your list until you remove it.", timeout=6, markup=False
            )
        if result.errors:
            self.notify(f"Could not check every page. {result.errors[0]}", severity="warning", timeout=8, markup=False)

    # --- watching a page ---------------------------------------------------------------------------------------------

    def action_watch(self) -> None:
        focused = self.focused
        if isinstance(focused, PageTable):
            page = focused.page_at_cursor()
            if page is not None:
                self.watch_page(*page)
                return
        self.action_watch_typed()

    def action_watch_typed(self) -> None:
        self.push_screen(AddPage(), self._page_typed)

    def _page_typed(self, text: str | None) -> None:
        if not text:
            return
        try:
            target = parse_watch_input(text, self.wiki)
        except WatchInputError as exc:
            self.notify(str(exc), severity="warning", timeout=6, markup=False)
            return
        self.watch_page(target.wiki, target.title)

    def watch_page(self, wiki: str, title: str) -> None:
        """Add a page in the background (it asks the wiki for the page ID) and say what happened in plain words."""
        self.run_worker(self._watch(wiki, title), name="watch", group="watch")

    async def _watch(self, wiki: str, title: str) -> None:
        shown = page_key(wiki, title)[1]
        try:
            result = await self.watchlist.add(wiki, title)
        except WatchError as exc:
            self.notify(str(exc), severity="warning", timeout=6, markup=False)
            return
        name = result.page.title
        if result.already_watched:
            self.notify(f"Already watching {name}", timeout=3, markup=False)
        elif result.redirected_from and result.redirected_from.casefold() != name.casefold():  # not mere capitals
            self.notify(f"{shown} redirects to {name}. Watching {name}.", timeout=5, markup=False)
        else:
            self.notify(f"Watching {name}", timeout=3, markup=False)

    # --- help --------------------------------------------------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen(self.help_lines()))

    def help_lines(self) -> list[tuple[str, str]]:
        """One line per active key we declare (read from the bindings), then the fixed lines."""
        nodes: dict[int, DOMNode] = {}
        for active in self.screen.active_bindings.values():
            nodes[id(active.node)] = active.node
        ours = {(b.key, b.action) for node in nodes.values() for b in declared_bindings(type(node))}
        lines: list[tuple[str, str]] = []
        for active in self.screen.active_bindings.values():
            binding = active.binding
            if not active.enabled or (binding.key, binding.action) not in ours or binding.action.startswith("tab("):
                continue
            lines.append((key_label(binding), help_text(binding)))
        return lines + FIXED_HELP

    # --- replay keys -------------------------------------------------------------------------------------------------

    def check_action(self, action: str, _parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("replay_"):
            return isinstance(self.source, ReplaySource)
        return True

    def replay_state(self) -> str:
        source = self.source
        if self.replay_finished:
            return REPLAY_FINISHED
        if not isinstance(source, ReplaySource):
            return ""
        return "paused" if source.paused else f"{source.speed:g}x"

    def _replay(self) -> ReplaySource | None:
        return self.source if isinstance(self.source, ReplaySource) else None

    def action_replay_pause(self) -> None:
        if (replay := self._replay()) is not None:
            if replay.paused:
                replay.resume()
            else:
                replay.pause()
            self._main.refresh_top_bar()

    def action_replay_faster(self) -> None:
        if (replay := self._replay()) is not None:
            replay.speed = replay.speed * 2
            self._main.refresh_top_bar()

    def action_replay_slower(self) -> None:
        if (replay := self._replay()) is not None:
            replay.speed = replay.speed / 2
            self._main.refresh_top_bar()

    def action_replay_skip(self) -> None:
        if (replay := self._replay()) is not None:
            replay.skip(60)
            self._main.refresh_top_bar()

    # --- messages from the tabs --------------------------------------------------------------------------------------

    def on_live_tab_filters_changed(self, message: LiveTab.FiltersChanged) -> None:
        self.update_config(
            hide_bots=message.hide_bots, articles_only=message.articles_only, min_bytes=message.min_bytes
        )

    def on_live_tab_wiki_changed(self, message: LiveTab.WikiChanged) -> None:
        self.wiki = message.wiki
        self._main.set_wiki(message.wiki)
        self.update_config(wiki=message.wiki)

    def update_config(self, **changes: Any) -> None:
        """Remember a setting for this run and, unless the app runs with --no-config, in the config file.

        Only the changed settings are written onto what is on disk, so a command-line choice such as ``--wiki`` is not
        saved as a side effect.
        """
        self.config = replace(self.config, **changes)
        if self.config_path is None:
            return
        try:
            on_disk, _ = load_config(self.config_path)
            save_config(replace(on_disk, **changes), self.config_path)
        except OSError as exc:
            self.notify(f"Could not save settings: {exc}", severity="warning", markup=False)
