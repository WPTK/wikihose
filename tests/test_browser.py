"""Opening a page in the web browser: the address builders and the check that comes before opening one.

No test here (or anywhere) opens a real browser: ``conftest.py`` replaces ``App.open_url`` for every test, and these
tests replace ``webbrowser.get`` and the environment.
"""

from __future__ import annotations

import webbrowser
from typing import Any

import pytest

from wikihose.titles import diff_url, page_url
from wikihose.ui.browser import SSH_VARIABLES, can_open_browser, open_in_browser

# --- the address of a page -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wiki", "title", "url"),
    [
        ("enwiki", "Ada Lovelace", "https://en.wikipedia.org/wiki/Ada_Lovelace"),
        ("enwiki", "ada_lovelace  ", "https://en.wikipedia.org/wiki/Ada_lovelace"),  # normalized like the watchlist
        ("enwiki", "Is this it?", "https://en.wikipedia.org/wiki/Is_this_it%3F"),
        ("enwiki", "AC/DC", "https://en.wikipedia.org/wiki/AC/DC"),
        ("enwiki", "Q&A", "https://en.wikipedia.org/wiki/Q%26A"),
        ("enwiki", "C#", "https://en.wikipedia.org/wiki/C%23"),
        ("enwiki", "100%", "https://en.wikipedia.org/wiki/100%25"),
        ("enwiki", "Ada (disambiguation)", "https://en.wikipedia.org/wiki/Ada_(disambiguation)"),
        ("enwiki", "Talk:Ada Lovelace", "https://en.wikipedia.org/wiki/Talk:Ada_Lovelace"),
        ("enwiki", "Z\u00fcrich", "https://en.wikipedia.org/wiki/Z%C3%BCrich"),
        ("dewiki", "Z\u00fcrich", "https://de.wikipedia.org/wiki/Z%C3%BCrich"),
        ("jawiki", "\u6771\u4eac", "https://ja.wikipedia.org/wiki/%E6%9D%B1%E4%BA%AC"),
        ("be_x_oldwiki", "Title", "https://be-x-old.wikipedia.org/wiki/Title"),
        (
            "frwiktionary",
            "chat",
            "https://fr.wiktionary.org/wiki/chat",
        ),  # a wiktionary keeps its lower-case first letter
    ],
)
def test_page_url(wiki: str, title: str, url: str) -> None:
    assert page_url(wiki, title) == url


@pytest.mark.parametrize("wiki", ["commonswiki", "wikidatawiki", "metawiki", "nonsense", ""])
def test_a_wiki_with_no_known_web_host_has_no_page_address(wiki: str) -> None:
    assert page_url(wiki, "Anything") is None
    assert diff_url(wiki, 1, 2) is None


def test_diff_url_names_the_newer_revision_as_the_diff_and_the_older_as_oldid() -> None:
    assert diff_url("enwiki", 100, 200) == "https://en.wikipedia.org/w/index.php?diff=200&oldid=100"
    assert diff_url("dewiki", 7, 9) == "https://de.wikipedia.org/w/index.php?diff=9&oldid=7"


# --- whether a browser can be opened here ----------------------------------------------------------------------------


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in SSH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def finds(monkeypatch: pytest.MonkeyPatch, browser: Any) -> None:
    monkeypatch.setattr(webbrowser, "get", lambda *_args, **_kwargs: browser)


def test_a_normal_desktop_browser_can_be_opened(clean_environment: pytest.MonkeyPatch) -> None:
    finds(clean_environment, webbrowser.BackgroundBrowser("firefox"))
    assert can_open_browser()
    finds(clean_environment, webbrowser.GenericBrowser("/usr/bin/xdg-open"))
    assert can_open_browser()


@pytest.mark.parametrize("name", SSH_VARIABLES)
def test_a_browser_is_not_opened_over_ssh(clean_environment: pytest.MonkeyPatch, name: str) -> None:
    finds(clean_environment, webbrowser.BackgroundBrowser("firefox"))
    clean_environment.setenv(name, "10.0.0.1 22 10.0.0.2 22")
    assert not can_open_browser()


def test_an_empty_ssh_variable_does_not_count(clean_environment: pytest.MonkeyPatch) -> None:
    finds(clean_environment, webbrowser.BackgroundBrowser("firefox"))
    clean_environment.setenv("SSH_TTY", "")
    assert can_open_browser()


def test_no_browser_at_all_cannot_be_opened(clean_environment: pytest.MonkeyPatch) -> None:
    def none(*_args: object, **_kwargs: object) -> None:
        raise webbrowser.Error("could not locate runnable browser")

    clean_environment.setattr(webbrowser, "get", none)
    assert not can_open_browser()


@pytest.mark.parametrize("name", ["lynx", "w3m", "links", "elinks", "www-browser", "/usr/bin/lynx", "w3m.exe"])
def test_a_browser_that_runs_in_this_terminal_is_not_opened(clean_environment: pytest.MonkeyPatch, name: str) -> None:
    finds(clean_environment, webbrowser.GenericBrowser(name))
    assert not can_open_browser()
    finds(clean_environment, webbrowser.BackgroundBrowser(name))
    assert not can_open_browser()


# --- open_in_browser -------------------------------------------------------------------------------------------------


class FakeApp:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.notices: list[tuple[str, dict[str, Any]]] = []

    def open_url(self, url: str) -> None:
        self.opened.append(url)

    def notify(self, message: str, **kwargs: Any) -> None:
        self.notices.append((message, kwargs))


def test_open_in_browser_opens_and_says_so(browser: Any) -> None:
    app = FakeApp()
    open_in_browser(app, "https://en.wikipedia.org/wiki/Ada", "Ada")  # type: ignore[arg-type]
    assert app.opened == ["https://en.wikipedia.org/wiki/Ada"]
    assert app.notices == [("Opening Ada in your browser.", {"timeout": 3, "markup": False})]


def test_open_in_browser_without_a_browser_shows_the_address_for_a_long_time(browser: Any) -> None:
    browser.available = False
    app = FakeApp()
    open_in_browser(app, "https://en.wikipedia.org/wiki/Ada", "Ada")  # type: ignore[arg-type]
    assert app.opened == []
    assert app.notices == [
        (
            "No browser can be opened here. The address is:\nhttps://en.wikipedia.org/wiki/Ada",
            {"timeout": 15, "markup": False},
        )
    ]


def test_open_in_browser_without_an_address_says_there_is_none(browser: Any) -> None:
    app = FakeApp()
    open_in_browser(app, None, "File:X.png")  # type: ignore[arg-type]
    assert app.opened == []
    assert [message for message, _ in app.notices] == ["There is no web address for File:X.png."]
    assert all(kwargs["markup"] is False for _, kwargs in app.notices)
