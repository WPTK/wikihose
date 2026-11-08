"""Helpers for the app tests: edits made by hand, a data source that plays a list, and a way to let the app settle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from wikihose.clock import Clock
from wikihose.config import Config
from wikihose.sources.events import Edit, StreamItem
from wikihose.ui.app import WikihoseApp
from wikihose.ui.tabs.live import LiveTab

EASTERN = ZoneInfo("America/New_York")
WHEN = datetime(2026, 11, 7, 19, 41, 5, tzinfo=UTC)


def make_edit(
    title: str = "Ada Lovelace",
    *,
    user: str = "Alice",
    bot: bool = False,
    namespace: int = 0,
    delta: int = 120,
    kind: str = "edit",
    comment: str = "fix a typo",
    wiki: str = "enwiki",
    server: str = "en.wikipedia.org",
    at: datetime = WHEN,
) -> Edit:
    old = 1000
    return Edit(
        wiki=wiki,
        title=title,
        namespace=namespace,
        user=user,
        bot=bot,
        kind="new" if kind == "new" else "edit",
        minor=False,
        old_len=None if kind == "new" else old,
        new_len=old + delta if kind != "new" else delta,
        rev_old=None,
        rev_new=None,
        comment=comment,
        at=at,
        server_name=server,
    )


class ListSource:
    """A live-looking source that plays its items and then ends."""

    name = "live"

    def __init__(self, clock: Clock, items: list[StreamItem] | None = None) -> None:
        self.clock = clock
        self.items_to_play = items or []
        self.closed = False

    async def items(self) -> AsyncIterator[StreamItem]:
        for item in self.items_to_play:
            yield item

    async def aclose(self) -> None:
        self.closed = True


def make_app(source: Any, clock: Clock, **kwargs: Any) -> WikihoseApp:
    config = kwargs.pop("config", Config())
    return WikihoseApp(source, config, clock=clock, tz=EASTERN, **kwargs)


def live_tab(app: WikihoseApp) -> LiveTab:
    return app.query_one(LiveTab)


async def settle(app: WikihoseApp, pilot: Any) -> LiveTab:
    """Let the stream worker finish, draw the log and the side column, and return the Live tab."""
    await app.workers.wait_for_complete()
    tab = live_tab(app)
    tab.flush_log()
    tab.refresh_side()
    await pilot.pause()
    return tab
