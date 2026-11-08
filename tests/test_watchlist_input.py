"""Reading what the user types after ``w``."""

from __future__ import annotations

import pytest

from wikihose.titles import host_for_wiki
from wikihose.watchlist_input import WatchInputError, WatchTarget, parse_watch_input


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ada Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
        ("ada lovelace", WatchTarget("enwiki", "Ada lovelace")),
        ("  Ada_Lovelace  ", WatchTarget("enwiki", "Ada Lovelace")),
        ("Ada   Lovelace#Early life", WatchTarget("enwiki", "Ada Lovelace")),
        ("Avengers: Doomsday", WatchTarget("enwiki", "Avengers: Doomsday")),
        ("https://en.wikipedia.org/wiki/Ada_Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
        ("https://en.wikipedia.org/wiki/Ada_Lovelace#Early_life", WatchTarget("enwiki", "Ada Lovelace")),
        ("https://en.m.wikipedia.org/wiki/Ada_Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
        ("https://de.wikipedia.org/wiki/Alan_Turing", WatchTarget("dewiki", "Alan Turing")),
        ("https://en.wikipedia.org/wiki/Caf%C3%A9_Society", WatchTarget("enwiki", "Café Society")),
        ("https://en.wikipedia.org/wiki/Ada%20Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
        (
            "https://en.wikipedia.org/w/index.php?title=Ada_Lovelace&action=history",
            WatchTarget("enwiki", "Ada Lovelace"),
        ),
        ("https://be-x-old.wikipedia.org/wiki/Test", WatchTarget("be_x_oldwiki", "Test")),
        ("https://fr.wiktionary.org/wiki/chat", WatchTarget("frwiktionary", "chat")),
        ("en.wikipedia.org/wiki/Ada_Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
        ("//en.wikipedia.org/wiki/Ada_Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
        ("HTTPS://EN.WIKIPEDIA.ORG/wiki/Ada_Lovelace", WatchTarget("enwiki", "Ada Lovelace")),
    ],
)
def test_valid_input(text: str, expected: WatchTarget) -> None:
    assert parse_watch_input(text, "enwiki") == expected


def test_a_bare_title_uses_the_default_wiki() -> None:
    assert parse_watch_input("Alan Turing", "dewiki") == WatchTarget("dewiki", "Alan Turing")
    assert parse_watch_input("chat", "frwiktionary") == WatchTarget("frwiktionary", "chat")  # case is kept


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("", "Type a page title"),
        ("   ", "Type a page title"),
        ("https://en.wikipedia.org/wiki/Special:Search?search=ada", "Special"),
        ("https://en.wikipedia.org/wiki/Talk:Ada_Lovelace", "Talk"),
        ("https://en.wikipedia.org/wiki/User_talk:Example", "User talk"),
        ("https://en.wikipedia.org/wiki/File:Ada.jpg", "File"),
        ("Category:British computer scientists", "Category"),
        ("wikipedia:Sandbox", "not articles"),
        ("https://example.com/wiki/Ada_Lovelace", "not to a Wikipedia article"),
        ("https://en.wikipedia.org/", "not to a Wikipedia article"),
        ("https://en.wikipedia.org/wiki/", "not to a Wikipedia article"),
        ("https://en.wikipedia.org/w/index.php?curid=974", "not to a Wikipedia article"),
        ("ftp://en.wikipedia.org/wiki/Ada", "not to a Wikipedia article"),
        ("https://commons.wikipedia.org/wiki/Ada", "cannot watch pages on"),
        ("Ada [[Lovelace]]", "not a valid page title"),
        ("Ada|Lovelace", "not a valid page title"),
        ("#Early life", "no page title"),
        ("A" * 300, "not a valid page title"),
    ],
)
def test_input_that_cannot_be_watched_explains_why(text: str, words: str) -> None:
    with pytest.raises(WatchInputError, match=words):
        parse_watch_input(text, "enwiki")


def test_a_default_wiki_without_a_known_host_is_refused() -> None:
    with pytest.raises(WatchInputError, match="cannot watch pages on commonswiki"):
        parse_watch_input("Ada", "commonswiki")


@pytest.mark.parametrize(
    ("wiki", "host"),
    [
        ("enwiki", "en.wikipedia.org"),
        ("be_x_oldwiki", "be-x-old.wikipedia.org"),
        ("zh_min_nanwiki", "zh-min-nan.wikipedia.org"),
        ("simplewiki", "simple.wikipedia.org"),
        ("frwiktionary", "fr.wiktionary.org"),
        ("commonswiki", None),
        ("wikidatawiki", None),
        ("metawiki", None),
        ("enwikisource", None),
        ("wiki", None),
        ("", None),
        ("EN.evil.example/wiki", None),
    ],
)
def test_host_for_wiki(wiki: str, host: str | None) -> None:
    assert host_for_wiki(wiki) == host
