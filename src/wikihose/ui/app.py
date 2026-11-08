"""The app shell: the top bar, the tab strip, the feed-health bar, the footer and the "make the window bigger" cover.

The shell does not know what is inside a tab. Tabs get the stream by subscribing to it; they tell the shell about
changes with messages. ``w`` asks the focused widget for its page (a ``PageTable``) and otherwise opens a dialog.
"""

from __future__ import annotations

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

from wikihose.clock import Clock
from wikihose.config import Config, load_config, save_config
from wikihose.sources.events import FeedStatus, StreamItem
from wikihose.sources.protocol import DataSource
from wikihose.sources.replay import ReplaySource
from wikihose.store import Store
from wikihose.titles import page_key, title_from_url
from wikihose.ui.bars import HealthBar, TopBar
from wikihose.ui.bindings import declared_bindings, help_text, key_label
from wikihose.ui.dialogs import FIXED_HELP, AddPage, HelpScreen
from wikihose.ui.tabs.live import LiveTab
from wikihose.ui.tabs.stub import STUBS, StubPane
from wikihose.ui.theme import THEMES
from wikihose.ui.widgets import PageTable
from wikihose.watchlist import Watchlist

if TYPE_CHECKING:
    from textual.dom import DOMNode

FLOOR = (80, 24)
TABS = ("live", "reading", "watchlist", "diff", "convergence")
REPLAY_FINISHED = "replay finished"


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
            for tab_id, (label, text) in STUBS.items():
                with TabPane(label, id=tab_id):
                    yield StubPane(text)
        yield HealthBar()
        yield Footer(show_command_palette=False, compact=True)
        yield TooSmall()

    def on_mount(self) -> None:
        self._check_size(self.size.width, self.size.height)

    def on_screen_resume(self) -> None:
        """Start the stream once every tab exists and has subscribed to it."""
        if not self._started:
            self._started = True
            self.shell.start_stream()

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
        Binding("w", "watch", "Watch", tooltip="Watch the selected page, or type a title or URL"),
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
        loop_replay: bool = False,
    ) -> None:
        super().__init__()
        self.source = source
        self.config = config
        self.clock = clock
        self.tz: tzinfo = tz or datetime.now().astimezone().tzinfo or UTC
        self.store = store
        self.config_path = config_path  # None: never write the config (--no-config)
        self.wiki = config.wiki
        self.watchlist = Watchlist()
        self.replay_finished = False
        self.loop_replay = loop_replay  # the demo: start the replay over when it ends, with an empty log
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

    def subscribe(self, callback: Callable[[StreamItem], None]) -> None:
        """Tabs call this to receive every item of the stream, in order, on the app's own event loop."""
        self._subscribers.append(callback)

    async def _pump(self) -> None:
        while True:
            async for item in self.source.items():
                self._dispatch(item)
            if not (self.loop_replay and isinstance(self.source, ReplaySource)):
                return
            self.source.rewind()
            self.query_one(LiveTab).start_over()
            self._main.refresh_top_bar()

    def _dispatch(self, item: StreamItem) -> None:
        if isinstance(item, FeedStatus):
            if isinstance(self.source, ReplaySource) and item.state == "down" and item.detail == REPLAY_FINISHED:
                if not self.loop_replay:
                    self.replay_finished = True  # the end of a recording is not a failure
                    self._main.refresh_top_bar()
            else:
                self._main.handle_status(item)
            return
        for callback in list(self._subscribers):
            callback(item)

    # --- tabs --------------------------------------------------------------------------------------------------------

    def action_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id
        self.call_after_refresh(self._main.focus_active_pane)

    def _watchlist_changed(self) -> None:
        self._main.set_badge(len(self.watchlist))

    # --- watching a page ---------------------------------------------------------------------------------------------

    def action_watch(self) -> None:
        focused = self.focused
        if isinstance(focused, PageTable):
            page = focused.page_at_cursor()
            if page is not None:
                self.watch_page(*page)
                return
        self.push_screen(AddPage(), self._page_typed)

    def _page_typed(self, text: str | None) -> None:
        if not text:
            return
        parsed = title_from_url(text)
        self.watch_page(*(parsed or (self.wiki, text)))

    def watch_page(self, wiki: str, title: str) -> None:
        shown = page_key(wiki, title)[1]
        if self.watchlist.add(wiki, title):
            self.notify(f"Watching {shown}", timeout=3)
        else:
            self.notify(f"Already watching {shown}", timeout=3)

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
            self.notify(f"Could not save settings: {exc}", severity="warning")
