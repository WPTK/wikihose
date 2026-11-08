"""The watchlist: one list of pages across all wikis, kept in the Store and indexed in memory.

A page is stored by (wiki, page ID) and matched live by (wiki, normalized title), so a live edit costs a dictionary
lookup and no request. The page ID comes from the Action API when the page is added; renames arrive as move events
(``apply_move``) or are found at startup by the catch-up (``catchup.py``), which changes the stored title.

``unseen`` counts edits newer than the revision the reader last looked at. ``record_edit`` ignores a revision that is
not newer than the newest one known, so an edit seen live and again in the catch-up counts once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from wikihose.clock import Clock
from wikihose.messages import describe_fetch_error
from wikihose.net import Client, FetchError
from wikihose.sources.events import Edit
from wikihose.store import Store, WatchedPage
from wikihose.titles import host_for_wiki, page_key

__all__ = ["AddResult", "WatchError", "WatchedPage", "Watchlist", "api_url"]


class WatchError(Exception):
    """Adding a page failed; ``str(error)`` says why in plain words."""


@dataclass(frozen=True, slots=True)
class AddResult:
    page: WatchedPage
    redirected_from: str | None  # the title that was asked for, when it turned out to be a redirect
    already_watched: bool


def api_url(wiki: str) -> str:
    """The Action API endpoint of a wiki. Raises ValueError for a database name wikihose cannot map to a host."""
    host = host_for_wiki(wiki)
    if host is None:
        raise ValueError(f"no known web address for {wiki}")
    return f"https://{host}/w/api.php"


class Watchlist:
    def __init__(self, store: Store, client: Client | None, clock: Clock) -> None:
        """``client`` is None when there is no network (a replay): pages can be matched and listed but not added."""
        self._store = store
        self._client = client
        self._clock = clock
        self._listeners: list[Callable[[], None]] = []
        self._pages: dict[tuple[str, int], WatchedPage] = {}
        self._titles: dict[tuple[str, str], int] = {}  # (wiki, normalized title) -> page ID
        for page in store.watched_pages():
            self._put(page)

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` after every change to the list or to a page's counts."""
        self._listeners.append(listener)

    def __contains__(self, page: object) -> bool:
        """``(wiki, title) in watchlist``, from memory."""
        return isinstance(page, tuple) and len(page) == 2 and self.match(*page) is not None

    def __len__(self) -> int:
        return len(self._pages)

    # --- reading -----------------------------------------------------------------------------------------------------

    def pages(self) -> list[WatchedPage]:
        """Every watched page, oldest first."""
        return sorted(self._pages.values(), key=lambda page: (page.added_at, page.wiki, page.page_id))

    def current(self, page: WatchedPage) -> WatchedPage | None:
        """The newest version of ``page`` (callers may hold an older copy), or None once it is removed."""
        return self._pages.get((page.wiki, page.page_id))

    def match(self, wiki: str, title: str) -> WatchedPage | None:
        """The watched page with this wiki and title, from memory."""
        page_id = self._titles.get(page_key(wiki, title))
        return None if page_id is None else self._pages[(wiki, page_id)]

    def unseen_total(self) -> int:
        """The number on the Watchlist tab's badge: edits not yet looked at, over all pages."""
        return sum(page.unseen for page in self._pages.values())

    # --- adding and removing -----------------------------------------------------------------------------------------

    async def add(self, wiki: str, title: str) -> AddResult:
        """Watch a page. Looks up its page ID, follows a redirect and refuses pages that do not exist or are not
        articles. A new page starts with nothing unseen."""
        try:
            url = api_url(wiki)
        except ValueError:
            raise WatchError(f"wikihose cannot watch pages on {wiki} yet.") from None
        if self._client is None:
            raise WatchError("Adding a page needs a connection to Wikipedia, and this session has none.")
        asked = page_key(wiki, title)[1]
        params = {
            "action": "query",
            "prop": "info",
            "titles": asked,
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        }
        try:
            data = await self._client.get_json(url, params)
        except FetchError as exc:
            raise WatchError(describe_fetch_error(exc)) from exc
        found = _first_page(data)
        if found is None:
            raise WatchError("Wikipedia did not answer with a page. Try again later.")
        if found.get("missing") or found.get("invalid") or "pageid" not in found:
            raise WatchError(f"There is no page called “{asked}”.")
        real = str(found["title"])
        if found.get("ns") != 0:
            raise WatchError(f"“{real}” is not an article. wikihose watches articles only.")
        page_id = int(found["pageid"])
        redirected_from = asked if real != asked else None
        existing = self._pages.get((wiki, page_id))
        if existing is not None:
            return AddResult(existing, redirected_from, already_watched=True)
        self._store.add_watch(wiki, page_id, real, self._clock.now(), int(found.get("lastrevid", 0)))
        page = self._store.watched_page(wiki, page_id)
        assert page is not None
        self._put(page)
        return AddResult(page, redirected_from, already_watched=False)

    def remove(self, page: WatchedPage) -> bool:
        """Stop watching. False when the page was not on the list."""
        current = self.current(page)
        if current is None:
            return False
        self._store.remove_watch(page.wiki, page.page_id)
        del self._pages[(page.wiki, page.page_id)]
        self._titles.pop(page_key(current.wiki, current.title), None)
        self._changed()
        return True

    # --- live changes ------------------------------------------------------------------------------------------------

    def record_edit(self, edit: Edit) -> WatchedPage | None:
        """Count a live edit to a watched page as unseen. Returns the updated page, or None when the page is not
        watched or the revision is not newer than the newest known."""
        page = self.match(edit.wiki, edit.title)
        if page is None or edit.rev_new is None or edit.rev_new <= page.last_rev_id:
            return None
        return self._save(replace(page, unseen=page.unseen + 1, last_rev_id=edit.rev_new, last_edit_at=edit.at))

    def apply_move(self, wiki: str, old_title: str, new_title: str) -> WatchedPage | None:
        """A page was moved: keep watching it under its new title. Returns the page, or None if it is not watched."""
        page = self.match(wiki, old_title)
        return None if page is None else self.rename(page, new_title)

    def rename(self, page: WatchedPage, new_title: str) -> WatchedPage:
        """Change a page's stored title. Used for move events and by the catch-up."""
        current = self.current(page) or page
        title = page_key(current.wiki, new_title)[1]
        self._titles.pop(page_key(current.wiki, current.title), None)
        self._store.rename_watch(current.wiki, current.page_id, title)
        return self._put(replace(current, title=title))

    # --- looking at a page -------------------------------------------------------------------------------------------

    def mark_seen(self, page: WatchedPage) -> WatchedPage:
        """The reader has looked at the page: everything up to the newest known revision is seen."""
        current = self.current(page) or page
        self._store.mark_watch_seen(current.wiki, current.page_id, self._clock.now())
        return self._put(replace(current, unseen=0, seen_rev_id=current.last_rev_id))

    # --- for the catch-up --------------------------------------------------------------------------------------------

    def set_deleted(self, page: WatchedPage, deleted: bool) -> WatchedPage:
        current = self.current(page) or page
        return self._save(replace(current, deleted=deleted))

    def absorb(self, page: WatchedPage, *, unseen: int, last_rev_id: int, last_edit_at: datetime | None) -> WatchedPage:
        """Take the counts the catch-up found. Never lowers what live edits already counted."""
        current = self.current(page) or page
        if last_rev_id > current.last_rev_id:
            current = replace(current, last_rev_id=last_rev_id, last_edit_at=last_edit_at or current.last_edit_at)
        return self._save(replace(current, unseen=max(current.unseen, unseen)))

    # --- internals ---------------------------------------------------------------------------------------------------

    def _put(self, page: WatchedPage) -> WatchedPage:
        self._pages[(page.wiki, page.page_id)] = page
        self._titles[page_key(page.wiki, page.title)] = page.page_id
        self._changed()
        return page

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _save(self, page: WatchedPage) -> WatchedPage:
        self._store.set_watch_state(
            page.wiki,
            page.page_id,
            unseen=page.unseen,
            last_rev_id=page.last_rev_id,
            last_edit_at=page.last_edit_at,
            deleted=page.deleted,
        )
        return self._put(page)


def _first_page(data: Any) -> dict[str, Any] | None:
    try:
        page = data["query"]["pages"][0]
    except (KeyError, IndexError, TypeError):
        return None
    return page if isinstance(page, dict) else None
