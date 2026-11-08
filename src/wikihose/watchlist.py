"""The watchlist, for now: an in-memory ordered set of pages. Session 3 persists it (see docs/deferred.md)."""

from __future__ import annotations

from collections.abc import Callable

from wikihose.titles import page_key


class Watchlist:
    def __init__(self) -> None:
        self._pages: dict[tuple[str, str], None] = {}  # insertion-ordered set
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` after every change."""
        self._listeners.append(listener)

    def add(self, wiki: str, title: str) -> bool:
        """Add a page; returns False when it was already there."""
        key = page_key(wiki, title)
        if key in self._pages:
            return False
        self._pages[key] = None
        for listener in list(self._listeners):
            listener()
        return True

    def __contains__(self, page: object) -> bool:
        if not (isinstance(page, tuple) and len(page) == 2):
            return False
        return page_key(*page) in self._pages

    def __len__(self) -> int:
        return len(self._pages)
