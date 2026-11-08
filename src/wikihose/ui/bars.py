"""The one-line bars of the shell: the top bar and the feed-health bar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, tzinfo

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.widgets import Static

from wikihose.sources.events import FeedStatus


class TopBar(Horizontal):
    """The app name, the wiki and the local date and time. In a replay it also says the data is recorded."""

    DEFAULT_CSS = """
    TopBar { height: 1; background: $panel; color: $foreground; padding: 0 1; }
    TopBar > Static { height: 1; width: auto; }
    TopBar > #topbar-left { width: 1fr; }
    """

    def __init__(
        self,
        now: Callable[[], datetime],
        tz: tzinfo,
        wiki: str,
        replay_state: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(id="topbar")
        self._now = now
        self._tz = tz
        self._wiki = wiki
        self._replay_state = replay_state

    def compose(self) -> ComposeResult:
        yield Static(id="topbar-left")
        yield Static(id="topbar-right")

    def on_mount(self) -> None:
        self.refresh_text()
        self.set_interval(1.0, self.refresh_text)

    def set_wiki(self, wiki: str) -> None:
        self._wiki = wiki
        self.refresh_text()

    def refresh_text(self) -> None:
        moment = self._now().astimezone(self._tz)
        when = f"{moment:%a} {moment.day} {moment:%b %H:%M}"
        left = Content.assemble(("wikihose", "bold"), f"  {self._wiki}  ", (when, "$text-muted"))
        self.query_one("#topbar-left", Static).update(left)
        right = "" if self._replay_state is None else f"Recorded data, not live  {self._replay_state()}"
        self.query_one("#topbar-right", Static).update(right)


class HealthBar(Static):
    """Hidden while every feed is ok. While one is not, names it and says why. It can also carry one line of progress
    (``set_note``), such as the startup check of the watched pages; a failing feed takes the line over."""

    DEFAULT_CSS = """
    HealthBar { height: 1; background: $panel; padding: 0 1; display: none; }
    HealthBar.-failing { display: block; }
    """

    def __init__(self) -> None:
        super().__init__(id="health")
        self._failing: dict[str, FeedStatus] = {}
        self._note = ""

    @property
    def failing(self) -> list[FeedStatus]:
        return list(self._failing.values())

    def report(self, status: FeedStatus) -> None:
        if status.state == "ok":
            self._failing.pop(status.feed, None)
        else:
            self._failing[status.feed] = status
        self._redraw()

    @property
    def note(self) -> str:
        return self._note

    def set_note(self, text: str) -> None:
        """Show ``text`` while all feeds are ok; an empty string removes it."""
        self._note = text
        self._redraw()

    def _redraw(self) -> None:
        self.set_class(bool(self._failing or self._note), "-failing")
        if not self._failing:
            self.update(Content.assemble((self._note, "$text-muted")))
            return
        parts: list[Content | tuple[str, str]] = []
        for status in self._failing.values():
            colour = "$error" if status.state == "down" else "$warning"
            if parts:
                parts.append(Content("   "))
            parts.append((status.message, colour))
        self.update(Content.assemble(*parts))
