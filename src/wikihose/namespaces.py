"""Tell articles from other pages in a wiki's title list.

The Pageviews top list mixes articles with ``Special:Search``, ``Wikipedia:`` and ``File:`` pages. A colon alone does
not make a page a non-article (``Ramayana:_Part_1`` is one), so a title is dropped only when the text before its first
colon is a real namespace name or alias of that wiki, taken from the Action API
(``meta=siteinfo&siprop=namespaces|namespacealiases|general``). This module is pure: it holds no network code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _norm(text: str) -> str:
    """Fold a namespace name or title prefix the way MediaWiki does: underscores are spaces, case is ignored."""
    return " ".join(text.replace("_", " ").split()).casefold()


@dataclass(frozen=True)
class NamespaceFilter:
    """The non-article namespace names of one wiki, plus its main page title."""

    prefixes: frozenset[str]
    main_page: str | None = None

    @classmethod
    def from_siteinfo(cls, payload: Mapping[str, Any]) -> NamespaceFilter:
        """Build a filter from a ``format=json`` siteinfo response (the whole body or its ``query`` part)."""
        query = payload.get("query", payload)
        prefixes: set[str] = set()
        for ns in query.get("namespaces", {}).values():
            if ns.get("id", 0) == 0:
                continue
            for key in ("*", "name", "canonical"):
                if ns.get(key):
                    prefixes.add(_norm(str(ns[key])))
        for alias in query.get("namespacealiases", []):
            if alias.get("id", 0) != 0 and alias.get("*"):
                prefixes.add(_norm(str(alias["*"])))
        main = query.get("general", {}).get("mainpage")
        return cls(frozenset(prefixes), _norm(main) if main else None)

    def is_article(self, title: str) -> bool:
        """True for a main-namespace page that is not the wiki's front page."""
        norm = _norm(title)
        if not norm or norm == self.main_page:
            return False
        head, sep, _ = norm.partition(":")
        return not (sep and head.strip() in self.prefixes)

    def articles(self, titles: list[str]) -> list[str]:
        return [t for t in titles if self.is_article(t)]
