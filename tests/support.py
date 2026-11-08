"""Helpers for the app tests: edits made by hand, a data source that plays a list, and a way to let the app settle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import respx
from payloads import action_page, action_response

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
        self.ended = False

    async def items(self) -> AsyncIterator[StreamItem]:
        for item in self.items_to_play:
            yield item
        self.ended = True

    async def aclose(self) -> None:
        self.closed = True


def make_app(source: Any, clock: Clock, **kwargs: Any) -> WikihoseApp:
    config = kwargs.pop("config", Config())
    return WikihoseApp(source, config, clock=clock, tz=EASTERN, **kwargs)


def live_tab(app: WikihoseApp) -> LiveTab:
    return app.query_one(LiveTab)


async def settle(app: WikihoseApp, pilot: Any) -> LiveTab:
    """Let the stream worker finish, draw the log and the side column, and return the Live tab."""
    await wait_for_stream(app, pilot)
    tab = live_tab(app)
    tab.flush_log()
    tab.refresh_side()
    await pilot.pause()
    return tab


async def wait_for_stream(app: WikihoseApp, pilot: Any) -> None:
    """Yield to the event loop until the source has played everything it had (the worker starts after the first draw)."""
    for _ in range(1000):
        if getattr(app.source, "ended", False) or app.replay_finished:
            return
        await pilot.pause()
    raise AssertionError("the source never finished")


async def footer_keys(pilot: Any, app: WikihoseApp, *, needing: str | None = None) -> list[Any]:
    """The footer's key widgets once they exist (and, with ``needing``, one whose label contains it).

    The footer rebuilds its keys asynchronously after bindings change, so a single ``pilot.pause()`` can be too early on
    a slow machine. This yields to the event loop until they are there, without any wall-clock wait.
    """
    for _ in range(50):
        keys = list(app.query("FooterKey"))
        if keys and (needing is None or any(needing in str(k.render()) for k in keys)):
            return keys
        await pilot.pause()
    seen = [str(k.render()) for k in app.query("FooterKey")]
    raise AssertionError(f"the footer never showed a key containing {needing!r}; it shows {seen}")


async def settled_footer_keys(pilot: Any, app: WikihoseApp) -> list[Any]:
    """The footer's keys once the rebuild after a tab change is done: every key laid out and the same set twice running.

    Right after a tab change the old tab's keys can still be there with no width, so a label every tab shows (Quit) is
    not enough to know the new footer is in.
    """
    previous: list[str] = []
    for _ in range(100):
        await pilot.pause()
        keys = list(app.query("FooterKey"))
        labels = [str(k.render()) for k in keys]
        if keys and all(k.region.width > 0 for k in keys) and labels == previous:
            return keys
        previous = labels
    raise AssertionError(f"the footer never settled; it shows {previous}")


def footer_text(keys: list[Any]) -> str:
    return " ".join(str(k.render()) for k in keys)


def action_api(*pages: tuple[int, str, int]) -> respx.Route:
    """Answer Action API page lookups (adding a page) for any Wikipedia from ``(page ID, title, newest revision)``.

    A title that differs only in case or underscores comes back under the real title, as the wiki normalizes it.
    """
    known = {title.lower(): action_page(page_id, title, rev) for page_id, title, rev in pages}

    def answer(request: httpx.Request) -> httpx.Response:
        asked = request.url.params["titles"].replace("_", " ")
        found = known.get(asked.lower(), {"ns": 0, "title": asked, "missing": True})
        return action_response(found)

    return respx.get(url__regex=r"https://[a-z-]+\.wikipedia\.org/w/api\.php").mock(side_effect=answer)


async def finish_work(app: WikihoseApp, pilot: Any) -> None:
    """Wait for the app's background workers (adding a page, the catch-up, a diff) and let the screen settle."""
    await app.workers.wait_for_complete()
    await pilot.pause()
