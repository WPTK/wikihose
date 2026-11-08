"""Page title handling: normalizing titles so the same page always gets the same key, and reading article URLs."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

_WHITESPACE = re.compile(r"\s+")
_HOST = re.compile(r"^(?P<lang>[a-z][a-z0-9-]*)(?:\.m)?\.(?P<project>wikipedia|wiktionary)\.org$")
_WIKI_NAMES = {"wikipedia": "wiki", "wiktionary": "wiktionary"}
_WIKIMEDIA_HOST = re.compile(
    r"[a-z0-9-]+(\.[a-z0-9-]+)*\.(wikipedia|wiktionary|wikibooks|wikiquote|wikisource|wikinews|wikiversity|wikivoyage"
    r"|wikimedia|wikidata|mediawiki)\.org"
)
_URL_SAFE = "/:()_,!*~@$;=+-."  # left unencoded in article links, as Wikipedia writes them


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


def valid_host(host: str) -> bool:
    """True only for a plain lowercase Wikimedia host such as en.wikipedia.org. The event's server_name is untrusted."""
    return _WIKIMEDIA_HOST.fullmatch(host) is not None


def article_url(server_name: str, title: str) -> str | None:
    """The article's address, such as https://en.wikipedia.org/wiki/Ada_Lovelace (spaces become underscores).

    None when ``server_name`` is not a Wikimedia host, so a hostile event can never put its own text into a link.
    """
    if not valid_host(server_name):
        return None
    return f"https://{server_name}/wiki/{quote(title.replace(' ', '_'), safe=_URL_SAFE)}"
