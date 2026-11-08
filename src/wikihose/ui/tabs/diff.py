"""The Diff tab: what changed between two revisions of a page, in red and green.

Other tabs ask for a diff by posting ``OpenDiff``; the shell switches to this tab and calls ``DiffTab.show``. When the
diff is of a watched page and reaches the newest revision wikihose knows about, showing it marks the page seen.
Escape goes back to the tab the reader came from (the shell does that, on ``DiffTab.Back``). Enter, or a double click
on the change, opens it on Wikipedia in the web browser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import tzinfo

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from wikihose.diff import DiffError, DiffHeader, fetch_diff, render_diff
from wikihose.net import Client
from wikihose.store import WatchedPage
from wikihose.titles import diff_url
from wikihose.ui.browser import open_in_browser
from wikihose.watchlist import Watchlist

log = logging.getLogger(__name__)

EMPTY = (
    "No change is open.\n\n"
    "On the Live tab, move to an edit and press d to see what it changed. "
    "On the Watchlist tab, press d on a page to see what changed since you last looked."
)
LOADING = "Loading the change…"
NO_NETWORK = "Showing a change needs a connection to Wikipedia, and this session has none."
BACK_HINT = "\n\nPress Escape to go back."


@dataclass(frozen=True, slots=True)
class DiffRequest:
    wiki: str
    from_rev: int
    to_rev: int
    header: DiffHeader  # header.title is also how a watched page is found


class OpenDiff(Message):
    """Posted by a tab to show a diff; the shell switches to the Diff tab."""

    def __init__(self, request: DiffRequest) -> None:
        super().__init__()
        self.request = request


class DiffTab(Vertical):
    class Back(Message):
        """The reader asked to leave the diff; the shell returns to the previous tab."""

    BINDINGS = [
        Binding("escape", "back", "Back", tooltip="Go back to the tab you came from"),
        Binding(
            "enter",
            "open_on_wiki",
            "Open",
            key_display="enter",
            tooltip="Open this change on Wikipedia",
        ),
    ]

    DEFAULT_CSS = """
    DiffTab { height: 1fr; }
    DiffTab VerticalScroll { height: 1fr; padding: 0 1; }
    DiffTab #diff-body { width: 100%; }
    """

    def __init__(self, client: Client | None, watchlist: Watchlist, tz: tzinfo) -> None:
        super().__init__(id="diff-tab")
        self._client = client
        self._watchlist = watchlist
        self._tz = tz
        self.request: DiffRequest | None = None
        self.state = "empty"  # empty, loading, shown or error
        self._page: WatchedPage | None = None  # the watched page the request is about, found when it was made

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="diff-scroll"):
            yield Static(Text(EMPTY, style="dim"), id="diff-body")

    def show(self, request: DiffRequest) -> None:
        """Start loading ``request``. A request made while another is loading replaces it."""
        self.request = request
        self.refresh_bindings()  # Enter has something to open now
        # Find the watched page now: a move while the diff loads would change the title it is found under.
        self._page = self._watchlist.match(request.wiki, request.header.title)
        self.state = "loading"
        self._set(Text(LOADING, style="dim"))
        self.run_worker(self._load(request), name="diff", group="diff", exclusive=True)

    async def _load(self, request: DiffRequest) -> None:
        if self._client is None:
            self._fail(NO_NETWORK)
            return
        try:
            diff = await fetch_diff(self._client, request.wiki, request.from_rev, request.to_rev)
        except DiffError as exc:
            self._fail(str(exc))
            return
        except Exception:  # an odd answer must cost this diff, not the app
            log.exception("showing a diff failed")
            self._fail("Something went wrong while showing that change.")
            return
        self.state = "shown"
        self._set(render_diff(diff, request.header, tz=self._tz))
        self.query_one("#diff-scroll", VerticalScroll).scroll_home(animate=False)
        page = None if self._page is None else self._watchlist.current(self._page)
        # Only a diff that covers everything unseen counts as having looked at the page.
        if page is not None and request.from_rev <= page.seen_rev_id and request.to_rev >= page.last_rev_id:
            self._watchlist.mark_seen(page)

    def _fail(self, message: str) -> None:
        self.state = "error"
        self._set(Text(message + BACK_HINT))

    def _set(self, content: Text) -> None:
        self.query_one("#diff-body", Static).update(content)

    def action_back(self) -> None:
        self.post_message(self.Back())

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "open_on_wiki":
            return self.request is not None  # nothing to open until a change has been asked for
        return super().check_action(action, parameters)

    def action_open_on_wiki(self) -> None:
        request = self.request
        if request is not None:
            url = diff_url(request.wiki, request.from_rev, request.to_rev)
            open_in_browser(self.app, url, f"the change to {request.header.title}")

    def on_click(self, event: events.Click) -> None:
        if event.chain == 2:
            self.action_open_on_wiki()

    @property
    def text(self) -> str:
        """What the tab shows now, as plain text (for tests)."""
        return str(self.query_one("#diff-body", Static).render())
