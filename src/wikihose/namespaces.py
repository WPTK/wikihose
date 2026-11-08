"""Tell articles from other pages in a wiki's title list.

The Pageviews top list mixes articles with ``Special:Search``, ``Wikipedia:`` and ``File:`` pages. A colon alone does
not make a page a non-article (``Ramayana:_Part_1`` is one), so a title is dropped only when the text before its first
colon is a real namespace name or alias of that wiki, taken from the Action API
(``meta=siteinfo&siprop=namespaces|namespacealiases|general``, ``formatversion`` 1 or 2). Interwiki prefixes (``wikt:``)
are not recognised: they do not appear in a wiki's own pageview list. This module is pure: it holds no network code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _norm(text: str) -> str:
    """Fold a namespace name or title prefix the way MediaWiki does: underscores are spaces, case is ignored."""
    return " ".join(text.replace("_", " ").split()).casefold()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class NamespaceFilter:
    """The non-article namespace names of one wiki, plus its main page title."""

    prefixes: frozenset[str]
    main_page: str | None = None

    @classmethod
    def from_siteinfo(cls, payload: Any) -> NamespaceFilter:
        """Build a filter from a ``format=json`` siteinfo response (the whole body or its ``query`` part).

        Anything that is not the expected shape is skipped, so a damaged response gives a filter that drops less.
        """
        query = payload.get("query", payload) if isinstance(payload, Mapping) else {}
        if not isinstance(query, Mapping):
            query = {}
        prefixes: set[str] = set()
        namespaces = query.get("namespaces")  # a dict in formatversion 1, a list in 2
        for ns in namespaces.values() if isinstance(namespaces, Mapping) else _list(namespaces):
            if not isinstance(ns, Mapping) or ns.get("id", 0) == 0:
                continue
            for key in ("*", "name", "canonical"):
                if ns.get(key):
                    prefixes.add(_norm(str(ns[key])))
        for alias in _list(query.get("namespacealiases")):
            if not isinstance(alias, Mapping):
                continue
            name = alias.get("*") or alias.get("alias")
            if alias.get("id", 0) != 0 and name:
                prefixes.add(_norm(str(name)))
        general = query.get("general")
        main = general.get("mainpage") if isinstance(general, Mapping) else None
        return cls(frozenset(prefixes), _norm(str(main)) if main else None)

    def is_article(self, title: str) -> bool:
        """True for a main-namespace page that is not the wiki's front page."""
        norm = _norm(title).lstrip(": ")  # a leading colon only forces the namespace reading
        if not norm or norm == self.main_page:
            return False
        head, sep, _ = norm.partition(":")
        return not (sep and head in self.prefixes)

    def articles(self, titles: list[str]) -> list[str]:
        return [t for t in titles if self.is_article(t)]
