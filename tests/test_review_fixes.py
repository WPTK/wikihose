"""Regression tests for the review of the watchlist, catch-up and diff modules."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from payloads import ADA_ID, ADA_LATEST, TURING_ID, action_page, make_edit

from wikihose.catchup import catch_up
from wikihose.clock import VirtualClock
from wikihose.diff import MAX_LINE_CHARS, DiffLine, RevisionDiff, Span, parse_diff, render_diff
from wikihose.net import Client
from wikihose.store import Store
from wikihose.watchlist import Watchlist

EN = "https://en.wikipedia.org/w/api.php"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "wikihose.db")
    yield db
    db.close()


def rev(revid: int, parent: int, size: int = 100, **extra: Any) -> dict[str, Any]:
    return {"revid": revid, "parentid": parent, "user": "U", "timestamp": "2026-11-06T00:00:00Z", "size": size, **extra}


def serve(
    info: dict[int, int], history: Callable[[int, httpx.Request], dict[str, Any]], titles: dict[int, str] | None = None
) -> None:
    """Info answers from {page id: lastrevid}; history from ``history(page id, request)`` (a response body)."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params["prop"] == "info":
            ids = [int(i) for i in params["pageids"].split("|")]
            pages = [action_page(i, (titles or {}).get(i, f"Page {i}"), info[i]) for i in ids]
            return httpx.Response(200, json={"query": {"pages": pages}})
        page_id = int(params["pageids"])
        return httpx.Response(200, json=history(page_id, request))

    respx.get(EN).mock(side_effect=handler)


def revisions_body(page_id: int, revisions: list[dict[str, Any]], **top: Any) -> dict[str, Any]:
    return {
        "query": {"pages": [{"pageid": page_id, "ns": 0, "title": f"Page {page_id}", "revisions": revisions}]},
        **top,
    }


# --- 1: a title is forgotten only by the page that holds it ----------------------------------------------------------


def test_renaming_or_removing_a_page_leaves_a_title_another_page_has_taken(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("enwiki", 1, "Foo", clock.now(), 10)
    store.add_watch("enwiki", 2, "Bar", clock.now(), 20)
    watchlist = Watchlist(store, client, clock)
    first, second = watchlist.pages()
    watchlist.rename(second, "Foo")  # a swap: page 2 now holds the title page 1 had
    assert watchlist.match("enwiki", "Foo") is not None and watchlist.match("enwiki", "Foo").page_id == 2  # type: ignore[union-attr]
    watchlist.rename(first, "Baz")  # page 1 leaves "Foo" behind; it must not take page 2's title with it
    assert watchlist.match("enwiki", "Foo").page_id == 2  # type: ignore[union-attr]
    assert watchlist.match("enwiki", "Baz").page_id == 1  # type: ignore[union-attr]


def test_removing_a_page_whose_title_was_reused_keeps_the_new_holder(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("enwiki", 1, "Foo", clock.now(), 10)
    watchlist = Watchlist(store, client, clock)
    (old,) = watchlist.pages()
    watchlist.remove(old)
    store.add_watch("enwiki", 9, "Foo", clock.now(), 50)  # deleted and re-created under a new page ID
    watchlist = Watchlist(store, client, clock)
    assert watchlist.match("enwiki", "Foo").page_id == 9  # type: ignore[union-attr]
    assert not watchlist.remove(old)  # the stale copy is not on the list
    assert watchlist.match("enwiki", "Foo").page_id == 9  # type: ignore[union-attr]


# --- 2 and 7: unseen adds up, and nothing new means no history request -----------------------------------------------


@respx.mock
async def test_catch_up_adds_to_the_unseen_count(store: Store, client: Client, clock: VirtualClock) -> None:
    store.add_watch("enwiki", ADA_ID, "Page 974", clock.now(), 100)
    watchlist = Watchlist(store, client, clock)
    watchlist.record_edit(make_edit(title="Page 974", rev_new=101))
    watchlist.record_edit(make_edit(title="Page 974", rev_new=102))
    serve({ADA_ID: 105}, lambda i, r: revisions_body(i, [rev(105, 104), rev(104, 103), rev(103, 102), rev(102, 101)]))

    result = await catch_up(watchlist, client, clock)

    assert [e.rev_id for e in result.edits] == [105, 104, 103]  # 102 was already counted live
    page = watchlist.pages()[0]
    assert (page.unseen, page.last_rev_id, page.seen_rev_id) == (5, 105, 100)
    assert watchlist.unseen_total() == 5


@respx.mock
async def test_nothing_newer_than_the_known_revision_costs_no_history_request(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("enwiki", ADA_ID, "Page 974", clock.now(), 100)
    store.set_watch_state("enwiki", ADA_ID, unseen=3, last_rev_id=ADA_LATEST, last_edit_at=None, deleted=False)
    calls: list[str] = []
    serve({ADA_ID: ADA_LATEST}, lambda i, r: calls.append("history") or {})
    watchlist = Watchlist(store, client, clock)

    result = await catch_up(watchlist, client, clock)

    assert calls == [] and result.edits == []
    assert watchlist.pages()[0].unseen == 3  # kept from the last run


# --- 3: the catch-up works on a snapshot -----------------------------------------------------------------------------


@respx.mock
async def test_a_page_removed_during_the_catch_up_stays_removed(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("enwiki", ADA_ID, "Page 974", clock.now(), 100)
    watchlist = Watchlist(store, client, clock)

    def history(page_id: int, request: httpx.Request) -> dict[str, Any]:
        watchlist.remove(watchlist.pages()[0])  # the user removes it while the request is in flight
        return revisions_body(page_id, [rev(101, 100)])

    serve({ADA_ID: 101}, history)
    await catch_up(watchlist, client, clock)
    assert watchlist.pages() == [] and store.watched_pages() == [] and watchlist.unseen_total() == 0


@respx.mock
async def test_marking_seen_during_the_catch_up_is_not_undone(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("enwiki", ADA_ID, "Page 974", clock.now(), 100)
    watchlist = Watchlist(store, client, clock)
    watchlist.record_edit(make_edit(title="Page 974", rev_new=101))

    def history(page_id: int, request: httpx.Request) -> dict[str, Any]:
        watchlist.mark_seen(watchlist.pages()[0])  # the reader opens it meanwhile
        return revisions_body(page_id, [rev(102, 101), rev(101, 100)])

    serve({ADA_ID: 102}, history)
    await catch_up(watchlist, client, clock)
    page = watchlist.pages()[0]
    assert (page.unseen, page.seen_rev_id, page.last_rev_id) == (1, 101, 102)  # only the edit after the look
    assert store.watched_pages() == [page]


def test_changes_to_a_removed_page_do_not_bring_it_back(store: Store, client: Client, clock: VirtualClock) -> None:
    store.add_watch("enwiki", 1, "Foo", clock.now(), 10)
    watchlist = Watchlist(store, client, clock)
    (page,) = watchlist.pages()
    watchlist.remove(page)
    watchlist.rename(page, "Bar")
    watchlist.mark_seen(page)
    watchlist.set_deleted(page, True)
    watchlist.absorb(page, unseen=4, last_rev_id=99, last_edit_at=None)
    assert watchlist.pages() == [] and store.watched_pages() == [] and watchlist.unseen_total() == 0


# --- 6: one bad answer or page does not stop the others --------------------------------------------------------------

BAD_HISTORIES: dict[str, Callable[[int, httpx.Request], dict[str, Any]]] = {
    "no revid": lambda i, r: revisions_body(i, [{"parentid": 1, "size": 1}]),
    "no timestamp": lambda i, r: revisions_body(i, [{"revid": 101, "parentid": 100, "size": 1}]),
    "bad timestamp": lambda i, r: revisions_body(i, [rev(101, 100, timestamp="yesterday")]),
    "no older size": lambda i, r: revisions_body(i, [rev(102, 101), {"revid": 101, "parentid": 100}]),
    "continue is a list": lambda i, r: revisions_body(i, [rev(101, 100)], **{"continue": ["x"]}),
    "pages is a string": lambda i, r: {"query": {"pages": "no"}},
}


@pytest.mark.parametrize("name", list(BAD_HISTORIES))
@respx.mock
async def test_a_malformed_history_is_an_error_for_that_page_only(
    name: str, store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("enwiki", ADA_ID, "Page 974", clock.now(), 100)
    clock.advance(1)
    store.add_watch("enwiki", TURING_ID, "Page 1208", clock.now(), 200)
    good = revisions_body(TURING_ID, [rev(201, 200)])
    serve({ADA_ID: 102, TURING_ID: 201}, lambda i, r: BAD_HISTORIES[name](i, r) if i == ADA_ID else good)
    watchlist = Watchlist(store, client, clock)

    result = await catch_up(watchlist, client, clock)

    assert len(result.errors) == 1 and result.errors[0].startswith("Page 974: Wikipedia sent an answer")
    assert [e.title for e in result.edits] == ["Page 1208"]
    assert watchlist.unseen_total() == 1


async def test_a_stored_wiki_that_cannot_be_mapped_to_a_host_is_an_error_not_a_crash(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    store.add_watch("commonswiki", 5, "Foo", clock.now(), 1)
    clock.advance(1)
    store.add_watch("enwiki", TURING_ID, "Page 1208", clock.now(), 200)
    with respx.mock:
        serve({TURING_ID: 201}, lambda i, r: revisions_body(i, [rev(201, 200)]))
        seen: list[tuple[int, int]] = []
        result = await catch_up(Watchlist(store, client, clock), client, clock, lambda d, t: seen.append((d, t)))
    assert result.errors == ["wikihose cannot check pages on commonswiki yet."]
    assert len(result.edits) == 1 and seen[-1] == (2, 2)


# --- 4 and 5: long lines and tabs in a changed line ------------------------------------------------------------------


def changed(text: str, *spans: Span) -> RevisionDiff:
    return RevisionDiff("enwiki", 1, 2, (DiffLine("changed", 1, text, spans),))


def marked(diff: RevisionDiff) -> dict[str, str]:
    text = render_diff(diff)
    return {text.plain[s.start : s.end]: str(s.style) for s in text.spans if str(s.style).startswith("bold ")}


def test_cutting_a_long_changed_line_keeps_its_highlights_in_view() -> None:
    text = "a" * 3000 + "NEW" + "b" * 2000
    diff = changed(text, Span(3000, 3003, added=True))
    rendered = render_diff(diff).plain.splitlines()[0]
    assert rendered.startswith("~ …a") and rendered.endswith("b…")  # cut at both ends, and it says so
    assert len(rendered) <= MAX_LINE_CHARS + 4
    assert marked(diff) == {"NEW": "bold green"}


def test_a_long_changed_line_from_a_real_payload_keeps_its_late_highlights() -> None:
    from payloads import fixture

    diff = parse_diff("enwiki", 1, 2, fixture("rest/compare_rewrite.json"))
    long = [line for line in diff.lines if line.spans and len(line.text) > MAX_LINE_CHARS]
    assert long  # the fixture does have such a line
    for line in long:
        first = line.spans[0]
        # the highlights nearest the start of the changes are drawn, and the cut is marked with an ellipsis
        assert line.text[first.start : first.end] in marked(RevisionDiff("enwiki", 1, 2, (line,)))
        assert "…" in render_diff(RevisionDiff("enwiki", 1, 2, (line,))).plain


def test_a_short_line_is_not_cut() -> None:
    assert render_diff(changed("short", Span(0, 5, added=False))).plain == "~ short\n"


def test_a_tab_in_a_changed_line_keeps_the_highlights_in_place() -> None:
    diff = changed("\tabc\told", Span(1, 4, added=True), Span(5, 8, added=False))
    assert render_diff(diff).plain == "~     abc old\n"
    assert marked(diff) == {"abc": "bold green", "old": "bold red strike"}


# --- nits: moved paragraphs are styled, and nothing is orange or a background -----------------------------------------


def test_moved_paragraph_lines_are_italic_red_and_green_and_never_orange() -> None:
    diff = parse_diff(
        "enwiki",
        1,
        2,
        {
            "diff": [
                {"type": 4, "text": "away"},
                {"type": 5, "lineNumber": 3, "text": "here"},
                {"type": 1, "lineNumber": 4, "text": "n"},
            ]
        },
    )
    text = render_diff(diff)
    assert text.plain.splitlines() == ["< away", "> here", "+ n"]
    styles = [str(s.style) for s in text.spans]
    assert "red italic" in styles and "green italic" in styles
    for style in styles:
        assert "orange" not in style and not any(word == "on" for word in style.split())
