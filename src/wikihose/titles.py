"""Page title handling: normalizing titles so the same page always gets the same key, and reading article URLs."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

_WHITESPACE = re.compile(r"\s+")
_HOST = re.compile(r"^(?P<lang>[a-z][a-z0-9-]*)(?:\.m)?\.(?P<project>wikipedia|wiktionary)\.org$")
_WIKI_NAMES = {"wikipedia": "wiki", "wiktionary": "wiktionary"}
_WIKI_NAME = re.compile(r"^(?P<lang>[a-z]{2,}(?:_[a-z0-9]+)*)(?P<project>wiktionary|wiki)$")
# Database names that look like a language edition but live on a different site.
_NOT_EDITIONS = frozenset(
    {
        "commons", "wikidata", "meta", "species", "mediawiki", "foundation", "outreach", "sources", "incubator",
        "nostalgia", "login", "vote", "test", "test2", "usability", "strategy", "wikimania", "labs", "office",
        "advisory", "collab", "internal", "private",
    }
)  # fmt: skip


def normalize_title(title: str, *, first_letter_upper: bool = True) -> str:
    """Underscores to spaces, whitespace runs collapsed, ends stripped, first character uppercased.

    Wiktionaries are case-sensitive; callers pass ``first_letter_upper=False`` for ``*wiktionary`` wikis.
    """
    text = _WHITESPACE.sub(" ", title.replace("_", " ")).strip()
    if first_letter_upper and text:
        text = text[0].upper() + text[1:]
    return text


def page_key(wiki: str, title: str) -> tuple[str, str]:
    """The (wiki, normalized title) pair that identifies a page. Wiktionary titles keep their case."""
    return wiki, normalize_title(title, first_letter_upper=not wiki.endswith("wiktionary"))


def title_from_url(text: str) -> tuple[str, str] | None:
    """Read ``(wiki, title)`` from an article URL such as https://en.wikipedia.org/wiki/Ada_Lovelace.

    Handles percent-encoding, the ``/w/index.php?title=`` form and mobile hosts (``en.m.``). Returns None for anything
    else. The wiki is a database name such as ``enwiki`` (language code with dashes turned to underscores).
    """
    try:
        parts = urlsplit(text.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    match = _HOST.match(host.lower())
    if match is None:
        return None
    wiki = match["lang"].replace("-", "_") + _WIKI_NAMES[match["project"]]
    title: str | None = None
    if parts.path.startswith("/wiki/"):
        title = unquote(parts.path[len("/wiki/") :])
    elif parts.path in ("/w/index.php", "/index.php"):
        values = parse_qs(parts.query).get("title")
        title = values[0] if values else None
    if not title:
        return None
    return page_key(wiki, title)


def host_for_wiki(wiki: str) -> str | None:
    """The web host of a wiki database name: ``enwiki`` gives en.wikipedia.org, ``be_x_oldwiki`` gives
    be-x-old.wikipedia.org and ``frwiktionary`` gives fr.wiktionary.org. None for anything else, such as
    ``commonswiki``.
    """
    match = _WIKI_NAME.match(wiki)
    if match is None or match["lang"] in _NOT_EDITIONS:
        return None
    project = "wikipedia" if match["project"] == "wiki" else "wiktionary"
    return f"{match['lang'].replace('_', '-')}.{project}.org"
