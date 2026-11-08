from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wikihose.namespaces import NamespaceFilter

FIXTURE = Path(__file__).parent / "fixtures" / "siteinfo" / "enwiki.json"


@pytest.fixture(scope="module")
def enwiki() -> NamespaceFilter:
    return NamespaceFilter.from_siteinfo(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "title",
    ["Ramayana:_Part_1", "Avengers:_Doomsday", "Abdul_El-Sayed", "Star_Wars:_The_Last_Jedi", "Special_Forces", "C++"],
)
def test_articles_are_kept(enwiki: NamespaceFilter, title: str) -> None:
    assert enwiki.is_article(title)


@pytest.mark.parametrize(
    "title",
    [
        "Special:Search",
        "Wikipedia:Featured_pictures",
        "Portal:Current_events",
        "File:Foo.jpg",
        "Help:Contents",
        "Talk:Cat",
        "Category:Cats",
        "Template:Infobox",
        "WP:AFD",  # alias
        "Image:Foo.jpg",  # alias
        "special:search",  # case-insensitive
        "Wikipedia_talk:Foo",
        "User_talk:Example",
        "Main_Page",
        ":Category:Cats",
    ],
)
def test_other_pages_are_dropped(enwiki: NamespaceFilter, title: str) -> None:
    assert not enwiki.is_article(title)


def test_articles_helper_keeps_order(enwiki: NamespaceFilter) -> None:
    titles = ["Main_Page", "Special:Search", "Ramayana:_Part_1", "Cat", "File:X.png"]
    assert enwiki.articles(titles) == ["Ramayana:_Part_1", "Cat"]


def test_empty_title_is_not_an_article(enwiki: NamespaceFilter) -> None:
    assert not enwiki.is_article("")


def test_accepts_the_query_part_alone() -> None:
    payload = {"namespaces": {"0": {"id": 0, "*": ""}, "6": {"id": 6, "*": "Datei", "canonical": "File"}}}
    flt = NamespaceFilter.from_siteinfo(payload)
    assert not flt.is_article("Datei:Foo.jpg")
    assert not flt.is_article("File:Foo.jpg")
    assert flt.is_article("Foo")


def test_accepts_formatversion_2_shapes() -> None:
    payload = {
        "namespaces": [{"id": 0, "name": ""}, {"id": 6, "name": "File", "canonical": "File"}],
        "namespacealiases": [{"id": 6, "alias": "Image"}],
    }
    flt = NamespaceFilter.from_siteinfo(payload)
    assert not flt.is_article("Image:Foo.jpg")
    assert flt.is_article("Foo")


@given(st.text())
def test_never_raises_and_a_plain_title_is_kept(title: str) -> None:
    flt = NamespaceFilter(frozenset({"special"}), "main page")
    flt.is_article(title)
    spaced = " ".join(
        title.replace("_", " ").split()
    )  # underscores are spaces in a title, and a blank title is no page at all
    if ":" not in title and spaced and spaced.casefold() != "main page":
        assert flt.is_article(title)


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(), lambda i: st.lists(i) | st.dictionaries(st.text(), i)
    )
)
def test_from_siteinfo_survives_any_json(payload: object) -> None:
    NamespaceFilter.from_siteinfo(payload).is_article("Anything:Goes")
