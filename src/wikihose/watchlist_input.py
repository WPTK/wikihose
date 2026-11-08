"""Reading what the user types after ``w``: a page title, or a pasted link to a Wikipedia article.

``parse_watch_input`` returns a ``WatchTarget`` or raises ``WatchInputError`` with a message in plain words. Only
article titles pass here; the wiki's own namespace names are not known offline, so the common English ones are caught
here and ``Watchlist.add`` checks the namespace the wiki reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from wikihose.titles import host_for_wiki, normalize_title, page_key, title_from_url

# English names and aliases of the namespaces that are not articles (talk pages are "<name> talk").
_NAMESPACES = frozenset(
    {
        "special",
        "media",
        "talk",
        "user",
        "wikipedia",
        "wp",
        "project",
        "file",
        "image",
        "mediawiki",
        "template",
        "help",
        "category",
        "portal",
        "draft",
        "timedtext",
        "module",
        "gadget",
        "gadget definition",
        "wiktionary",
        "appendix",
        "index",
        "thesaurus",
        "reconstruction",
        "citations",
    }
)
_BAD_CHARACTERS = re.compile(r"[\[\]{}|<>\x00-\x1f\x7f]")
_BARE_HOST = re.compile(r"^(?:[a-z][a-z0-9-]*\.)(?:m\.)?(?:wikipedia|wiktionary)\.org/", re.IGNORECASE)
MAX_TITLE_BYTES = 255


class WatchInputError(ValueError):
    """The text cannot name a page to watch; ``str(error)`` says why in plain words."""


@dataclass(frozen=True, slots=True)
class WatchTarget:
    wiki: str
    title: str


def namespace_prefix(title: str) -> str | None:
    """The leading ``Name:`` of a title if it is a known non-article namespace (``Talk:``, ``User talk:`` ...)."""
    prefix, colon, _ = title.partition(":")
    if not colon:
        return None
    name = normalize_title(prefix).lower()
    if name.endswith(" talk"):
        name = name[: -len(" talk")]
    return prefix.strip() if name in _NAMESPACES else None


def parse_watch_input(text: str, default_wiki: str) -> WatchTarget:
    """A bare title (``ada lovelace``, ``Ada_Lovelace``) on ``default_wiki``, or an article URL on its own wiki."""
    text = text.strip()
    if not text:
        raise WatchInputError("Type a page title or paste a Wikipedia link.")
    if _BARE_HOST.match(text):
        text = "https://" + text
    elif text.startswith("//"):
        text = "https:" + text
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.IGNORECASE):
        found = title_from_url(text)
        if found is None:
            raise WatchInputError(
                "That link is not to a Wikipedia article. Paste an address like en.wikipedia.org/wiki/Ada_Lovelace."
            )
        wiki, title = found
    else:
        wiki, title = page_key(default_wiki, text.split("#", 1)[0])
    if host_for_wiki(wiki) is None:
        raise WatchInputError(f"wikihose cannot watch pages on {wiki} yet.")
    if not title:
        raise WatchInputError("That has no page title in it.")
    prefix = namespace_prefix(title)
    if prefix is not None:
        raise WatchInputError(f"{prefix}: pages are not articles. wikihose watches articles only.")
    if _BAD_CHARACTERS.search(title) or len(title.encode()) > MAX_TITLE_BYTES:
        raise WatchInputError("That is not a valid page title.")
    return WatchTarget(wiki, title)
