"""Pilot tests for the Watchlist tab, the Diff tab, live matching and the startup catch-up."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from payloads import ADA_ID, ADA_LATEST, ADA_PREVIOUS, TURING_ID, TURING_LATEST, action_page, fixture
from payloads import make_edit as ada_edit
from support import ListSource, action_api, finish_work, make_app, make_edit, settle
from textual.widgets import DataTable, TabbedContent

from wikihose.clock import VirtualClock
from wikihose.net import Client
from wikihose.sources.events import Edit, PageMove
from wikihose.sources.recording import sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.store import Store
from wikihose.ui.app import WikihoseApp
from wikihose.ui.bars import HealthBar
from wikihose.ui.dialogs import AddPage, ConfirmRemove
from wikihose.ui.tabs.diff import DiffTab

EN_API = "https://en.wikipedia.org/w/api.php"
COMPARE = f"https://en.wikipedia.org/w/rest.php/v1/revision/{ADA_PREVIOUS}/compare/{ADA_LATEST}"
SIZE = (100, 30)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "wikihose.db")
    yield db
    db.close()


def watch_ada(store: Store, clock: VirtualClock, *, unseen: int = 0) -> None:
    """Ada Lovelace, seen up to ADA_PREVIOUS; with ``unseen`` the newest revision has arrived."""
    store.add_watch("enwiki", ADA_ID, "Ada Lovelace", clock.now(), ADA_PREVIOUS)
    if unseen:
        edit_at = datetime(2026, 11, 6, 19, 2, 23, tzinfo=UTC)
        store.set_watch_state(
            "enwiki", ADA_ID, unseen=unseen, last_rev_id=ADA_LATEST, last_edit_at=edit_at, deleted=False
        )


def app_for(clock: VirtualClock, store: Store, client: Client | None, *items: Any) -> WikihoseApp:
    return make_app(ListSource(clock, list(items)), clock, store=store, client=client)


def badge(app: WikihoseApp) -> str:
    return str(app.query_one(TabbedContent).get_tab("watchlist").label_text)


def serve_compare() -> respx.Route:
    return respx.get(COMPARE).mock(return_value=httpx.Response(200, json=fixture("rest/compare_small_edit.json")))


def table(app: WikihoseApp) -> DataTable[Any]:
    return app.query_one("#watch-table", DataTable)


def notes(app: WikihoseApp) -> list[str]:
    return [n.message for n in app._notifications]


# --- the Watchlist tab -------------------------------------------------------------------------------------------------


async def test_an_empty_watchlist_says_how_to_add_a_page(clock: VirtualClock, store: Store) -> None:
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        text = str(app.query_one("#watch-empty").render())
        assert "You are not watching any pages yet." in text
        assert "press w" in text and "Press W" in text
        assert app.query_one("#watch-empty").display
        assert not table(app).display


async def test_the_tab_lists_each_page_with_its_unseen_count_and_the_badge_adds_them_up(
    clock: VirtualClock, store: Store
) -> None:
    watch_ada(store, clock, unseen=3)
    clock.advance(1)  # the list is oldest first
    store.add_watch("dewiki", 5, "Seite", clock.now(), 10)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert table(app).row_count == 2
        assert [str(c) for c in table(app).get_row_at(0)][:3] == ["enwiki", "Ada Lovelace", "3"]
        assert [str(c) for c in table(app).get_row_at(1)][:3] == ["dewiki", "Seite", "·"]
        assert "06 Nov 14:02" in str(table(app).get_row_at(0)[3])  # 19:02 UTC in New York
        assert badge(app) == "Watchlist 3"


async def test_a_deleted_page_says_so_in_words(clock: VirtualClock, store: Store) -> None:
    watch_ada(store, clock)
    store.set_watch_state("enwiki", ADA_ID, unseen=0, last_rev_id=ADA_PREVIOUS, last_edit_at=None, deleted=True)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert str(table(app).get_row_at(0)[-1]) == "deleted"


@respx.mock
async def test_enter_opens_the_diff_from_the_revision_last_seen_and_marks_the_page_seen(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock, unseen=1)
    route = serve_compare()
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("enter")
        await finish_work(app, pilot)
        assert app.query_one(TabbedContent).active == "diff"
        assert route.call_count == 1
        text = app.query_one(DiffTab).text
        assert "Ada Lovelace" in text and f"Revisions {ADA_PREVIOUS} to {ADA_LATEST}" in text
        assert badge(app) == "Watchlist"  # opening it cleared the unseen edit
        (page,) = store.watched_pages()
        assert (page.unseen, page.seen_rev_id) == (0, ADA_LATEST)


async def test_enter_on_a_page_with_nothing_new_says_so(clock: VirtualClock, store: Store) -> None:
    watch_ada(store, clock)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "watchlist"
        assert notes(app) == ["Nothing new on Ada Lovelace since you last looked."]


async def test_m_marks_the_selected_page_seen(clock: VirtualClock, store: Store) -> None:
    watch_ada(store, clock, unseen=2)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("m")
        await pilot.pause()
        assert badge(app) == "Watchlist"
        assert store.watched_pages()[0].unseen == 0
        assert notes(app) == ["Marked Ada Lovelace as seen."]


@pytest.mark.parametrize("key", ["d", "delete"])
async def test_d_and_delete_remove_a_page_after_a_question(clock: VirtualClock, store: Store, key: str) -> None:
    watch_ada(store, clock, unseen=1)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRemove)
        assert len(app.watchlist) == 1  # nothing happens until the answer
        await pilot.click("#remove")
        await pilot.pause()
        assert len(app.watchlist) == 0 and store.watched_pages() == []
        assert badge(app) == "Watchlist"
        assert app.query_one("#watch-empty").display
        assert notes(app) == ["Stopped watching Ada Lovelace."]


@pytest.mark.parametrize("answer", ["escape", "keep"])
async def test_the_remove_question_can_be_cancelled(clock: VirtualClock, store: Store, answer: str) -> None:
    watch_ada(store, clock)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("d")
        await pilot.pause()
        if answer == "keep":
            await pilot.click("#keep")
        else:
            await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmRemove)
        assert len(app.watchlist) == 1


@respx.mock
async def test_w_on_the_watchlist_tab_opens_the_prompt(clock: VirtualClock, store: Store, client: Client) -> None:
    watch_ada(store, clock)
    action_api((TURING_ID, "Alan Turing", TURING_LATEST))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, AddPage)
        await pilot.press(*"Alan Turing", "enter")
        await finish_work(app, pilot)
        assert table(app).row_count == 2


@respx.mock
async def test_adding_a_redirect_says_where_it_went(clock: VirtualClock, store: Store, client: Client) -> None:
    respx.get(EN_API).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [action_page(ADA_ID, "Ada Lovelace", ADA_LATEST)]}})
    )
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        await pilot.press(*"Augusta Ada King", "enter")
        await finish_work(app, pilot)
        assert notes(app) == ["Augusta Ada King redirects to Ada Lovelace. Watching Ada Lovelace."]


# --- the Diff tab ------------------------------------------------------------------------------------------------------


async def test_the_diff_tab_with_nothing_open_says_how_to_open_one(clock: VirtualClock, store: Store) -> None:
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("4")
        await pilot.pause()
        assert "No change is open." in app.query_one(DiffTab).text
        assert "press d" in app.query_one(DiffTab).text


@respx.mock
async def test_d_on_a_live_edit_shows_its_diff_and_b_goes_back(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    route = serve_compare()
    app = app_for(clock, store, client, _with_revisions(ada_edit("Ada Lovelace")))
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("d")
        await finish_work(app, pilot)
        assert app.query_one(TabbedContent).active == "diff"
        assert route.call_count == 1
        assert "Revisions" in app.query_one(DiffTab).text
        await pilot.press("b")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "live"


def _with_revisions(edit: Edit) -> Edit:
    return Edit(
        wiki=edit.wiki,
        title=edit.title,
        namespace=0,
        user=edit.user,
        bot=False,
        kind="edit",
        minor=False,
        old_len=edit.old_len,
        new_len=edit.new_len,
        rev_old=ADA_PREVIOUS,
        rev_new=ADA_LATEST,
        comment=edit.comment,
        at=edit.at,
        server_name=edit.server_name,
    )


@respx.mock
async def test_escape_leaves_the_diff_for_the_tab_it_came_from(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock, unseen=1)
    serve_compare()
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("enter")
        await finish_work(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "watchlist"


@respx.mock
async def test_a_diff_that_cannot_be_loaded_says_why_in_plain_words_and_leaves_the_page_unseen(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock, unseen=1)
    respx.get(COMPARE).mock(return_value=httpx.Response(404))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("enter")
        await finish_work(app, pilot)
        tab = app.query_one(DiffTab)
        assert tab.state == "error"
        assert "That change could not be found." in tab.text and "Press b to go back." in tab.text
        assert badge(app) == "Watchlist 1"


@respx.mock
async def test_a_diff_that_is_loading_says_so(clock: VirtualClock, store: Store, client: Client) -> None:
    watch_ada(store, clock, unseen=1)
    gate = asyncio.Event()

    async def slow(_: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=fixture("rest/compare_small_edit.json"))

    respx.get(COMPARE).mock(side_effect=slow)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(DiffTab).state == "loading"
        assert "Loading the change" in app.query_one(DiffTab).text
        gate.set()
        await finish_work(app, pilot)
        assert app.query_one(DiffTab).state == "shown"


async def test_d_on_a_new_page_explains_there_is_nothing_to_compare(clock: VirtualClock, store: Store) -> None:
    app = app_for(clock, store, None, make_edit("Brand New", kind="new"))
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("d")
        await pilot.pause()
        assert notes(app) == ["That edit created the page, so there is nothing to compare it with."]
        assert app.query_one(TabbedContent).active == "live"


async def test_d_on_an_edit_without_revision_numbers_explains(clock: VirtualClock, store: Store) -> None:
    app = app_for(clock, store, None, make_edit("Ada Lovelace"))
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("d")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "live"
        assert len(notes(app)) == 1


# --- live matching -----------------------------------------------------------------------------------------------------


@respx.mock  # no routes: any request at all fails the test
async def test_live_edits_to_watched_pages_count_as_unseen_without_any_request(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock)
    items = [
        ada_edit("Ada Lovelace", rev_new=ADA_LATEST + 1),
        ada_edit("Ada Lovelace", rev_new=ADA_LATEST + 2),
        ada_edit("Ada Lovelace", rev_new=ADA_LATEST + 2),  # the same revision again counts once
        ada_edit("Grace Hopper", rev_new=5),  # not watched
        ada_edit("Ada Lovelace", rev_new=ADA_LATEST + 1, wiki="dewiki"),  # another wiki's page of that name
    ]
    app = app_for(clock, store, client, *items)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert app.watchlist.unseen_total() == 2
        assert badge(app) == "Watchlist 2"
        (page,) = store.watched_pages()
        assert (page.unseen, page.last_rev_id) == (2, ADA_LATEST + 2)
        assert app.last_editor(page) == "Someone"
        assert respx.calls.call_count == 0


@respx.mock
async def test_a_watched_edit_is_marked_in_the_log_and_others_are_not(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock)
    app = app_for(clock, store, client, ada_edit("Ada Lovelace"), ada_edit("Grace Hopper", rev_new=9))
    async with app.run_test(size=SIZE) as pilot:
        tab = await settle(app, pilot)
        cells = {edit.title: app.query_one("#log", DataTable).get_cell(key, "Article") for key, edit in tab._rows}
        assert cells["Ada Lovelace"].plain.startswith("★ ")
        assert not cells["Grace Hopper"].plain.startswith("★")


@respx.mock
async def test_a_move_keeps_the_page_watched_under_its_new_title(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock)
    move = PageMove("enwiki", "Ada Lovelace", "Ada King", 0, "Mover", datetime(2026, 11, 7, 12, 31, tzinfo=UTC))
    app = app_for(clock, store, client, move, ada_edit("Ada King", rev_new=ADA_LATEST + 1))
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        assert ("enwiki", "Ada King") in app.watchlist and ("enwiki", "Ada Lovelace") not in app.watchlist
        assert store.watched_pages()[0].title == "Ada King"
        assert app.watchlist.unseen_total() == 1  # the edit after the move was matched under the new title
        assert "Ada Lovelace was moved to Ada King. Still watching it." in notes(app)
        assert respx.calls.call_count == 0


# --- the startup catch-up ----------------------------------------------------------------------------------------------


class Wiki:
    """Page information for the catch-up: Ada has one new revision."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params["prop"] == "info":
            ids = [int(i) for i in params["pageids"].split("|")]
            pages = [{"pageid": i, "ns": 0, "title": "Ada Lovelace", "lastrevid": ADA_LATEST} for i in ids]
            return httpx.Response(200, json={"query": {"pages": pages}})
        history = fixture("actionapi/revisions_one.json")
        revisions = history["query"]["pages"][0]["revisions"][:1]
        page = {**history["query"]["pages"][0], "revisions": revisions}
        return httpx.Response(200, json={"query": {"pages": [page]}})


@respx.mock
async def test_the_catch_up_runs_after_startup_and_feeds_the_badge(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock)
    route = respx.get(EN_API).mock(side_effect=Wiki())
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await finish_work(app, pilot)
        assert route.call_count == 2  # page information, then the one changed page's history
        assert badge(app) == "Watchlist 1"
        assert "1 of 1 watched page changed while you were away." in notes(app)
        assert app.query_one(HealthBar).note == ""  # the progress line is gone again
        assert not app.query_one(HealthBar).display


@respx.mock
async def test_the_catch_up_shows_progress_in_the_status_bar_while_it_runs(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock)
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return Wiki()(request)

    respx.get(EN_API).mock(side_effect=slow)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.pause()
        bar = app.query_one(HealthBar)
        assert bar.note == "Checking 1 watched page… 0 of 1"
        assert bar.display
        gate.set()
        await finish_work(app, pilot)
        assert bar.note == ""


@respx.mock
async def test_the_catch_up_does_not_block_startup_and_is_cancelled_on_quit(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    watch_ada(store, clock)
    started = asyncio.Event()

    async def never(_: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    respx.get(EN_API).mock(side_effect=never)
    source = ListSource(clock)
    app = make_app(source, clock, store=store, client=client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await asyncio.wait_for(started.wait(), 5)
        assert app.is_running and source.ended  # the stream ran while the check was still waiting
        await pilot.press("q")
    assert not app.is_running
    assert source.closed


@respx.mock
async def test_a_failed_catch_up_says_so_without_crashing(clock: VirtualClock, store: Store, client: Client) -> None:
    watch_ada(store, clock)
    respx.get(EN_API).mock(side_effect=httpx.ConnectError("no route"))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await finish_work(app, pilot)
        assert any(m.startswith("Could not check every page. Wikipedia could not be reached.") for m in notes(app))
        assert app.is_running


@respx.mock
async def test_no_catch_up_in_a_replay(clock: VirtualClock, store: Store, client: Client) -> None:
    watch_ada(store, clock, unseen=1)
    with sample_recording() as path:
        app = make_app(ReplaySource(path, clock), clock, store=store, client=client)
        async with app.run_test(size=SIZE) as pilot:
            await finish_work(app, pilot)
            assert respx.calls.call_count == 0
            assert badge(app) == "Watchlist 1"  # a replay still matches live edits against the list


@respx.mock
async def test_no_catch_up_without_a_client_or_without_pages(clock: VirtualClock, store: Store, client: Client) -> None:
    watch_ada(store, clock)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await finish_work(app, pilot)
        assert notes(app) == []
    app = app_for(clock, Store(":memory:"), client)
    async with app.run_test(size=SIZE) as pilot:
        await finish_work(app, pilot)
        assert respx.calls.call_count == 0


# --- footer, help and mouse --------------------------------------------------------------------------------------------


@respx.mock
async def test_clicking_the_footer_keys_of_the_live_diff_watchlist_and_diff_tabs(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    from test_app_shell import click_footer

    watch_ada(store, clock, unseen=1)
    serve_compare()
    app = app_for(clock, store, client, _with_revisions(ada_edit("Ada Lovelace")))
    async with app.run_test(size=(140, 30)) as pilot:
        await settle(app, pilot)
        await click_footer(pilot, app, "Diff")  # Live: show what the selected edit changed
        await finish_work(app, pilot)
        assert app.query_one(TabbedContent).active == "diff"
        await click_footer(pilot, app, "Back")  # Diff: back to Live
        assert app.query_one(TabbedContent).active == "live"
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.pause()
        await click_footer(pilot, app, "Seen")  # Watchlist: mark seen
        assert store.watched_pages()[0].unseen == 0
        await click_footer(pilot, app, "Remove")  # Watchlist: remove, with the question first
        assert isinstance(app.screen, ConfirmRemove)


async def test_help_on_the_watchlist_and_diff_tabs_lists_their_keys(clock: VirtualClock, store: Store) -> None:
    watch_ada(store, clock)
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        table(app).focus()
        await pilot.press("question_mark")
        await pilot.pause()
        keys = [str(line.render()).split()[0] for line in app.screen.query(".help-line")]
        assert keys == ["m", "d", "delete", "w", "W", "?", "q", "1-5", "Enter", "Tab", "Mouse"]
        await pilot.press("escape", "4")
        await pilot.pause()
        app.query_one("#diff-scroll").focus()
        await pilot.press("question_mark")
        await pilot.pause()
        keys = [str(line.render()).split()[0] for line in app.screen.query(".help-line")]
        assert keys[:3] == ["escape", "b", "w"]
