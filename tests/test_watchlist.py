"""The watchlist service: adding, the in-memory match, live edits, moves and the unseen counts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from payloads import ADA_ID, ADA_LATEST, action_page, action_response, fixture, make_edit

from wikihose.clock import VirtualClock
from wikihose.net import Client
from wikihose.store import Store
from wikihose.watchlist import WatchError, Watchlist, api_url

API = "https://en.wikipedia.org/w/api.php"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "wikihose.db")
    yield db
    db.close()


@pytest.fixture
def watchlist(store: Store, client: Client, clock: VirtualClock) -> Watchlist:
    return Watchlist(store, client, clock)


def serve(name: str) -> respx.Route:
    return respx.get(API).mock(return_value=httpx.Response(200, json=fixture(f"actionapi/{name}")))


@respx.mock
async def test_add_looks_up_the_page_id_and_starts_with_nothing_unseen(
    watchlist: Watchlist, clock: VirtualClock
) -> None:
    route = serve("info_existing.json")
    result = await watchlist.add("enwiki", "ada lovelace")
    page = result.page
    assert (page.wiki, page.page_id, page.title) == ("enwiki", ADA_ID, "Ada Lovelace")
    assert (page.unseen, page.last_rev_id, page.seen_rev_id, page.deleted) == (0, ADA_LATEST, ADA_LATEST, False)
    assert page.added_at == clock.now()
    assert not result.already_watched
    assert result.redirected_from == "Ada lovelace"  # the typed title differed from the real one
    assert dict(route.calls.last.request.url.params) == {
        "action": "query",
        "prop": "info",
        "titles": "Ada lovelace",
        "redirects": "1",
        "format": "json",
        "formatversion": "2",
    }
    assert watchlist.unseen_total() == 0


@respx.mock
async def test_add_with_the_exact_title_is_not_a_redirect(watchlist: Watchlist) -> None:
    serve("info_existing.json")
    result = await watchlist.add("enwiki", "Ada_Lovelace")
    assert result.redirected_from is None


@respx.mock
async def test_add_follows_a_redirect_and_says_so(watchlist: Watchlist) -> None:
    serve("info_redirect.json")
    result = await watchlist.add("enwiki", "Augusta Ada King")
    assert result.page.title == "Ada Lovelace" and result.page.page_id == ADA_ID
    assert result.redirected_from == "Augusta Ada King"
    assert watchlist.match("enwiki", "Ada Lovelace") == result.page
    assert watchlist.match("enwiki", "Augusta Ada King") is None


@respx.mock
async def test_adding_the_same_page_twice_keeps_one_entry(watchlist: Watchlist) -> None:
    serve("info_existing.json")
    first = await watchlist.add("enwiki", "Ada Lovelace")
    second = await watchlist.add("enwiki", "Ada Lovelace")
    assert second.already_watched and second.page == first.page
    assert len(watchlist.pages()) == 1


@respx.mock
async def test_adding_a_missing_page_is_an_error(watchlist: Watchlist) -> None:
    serve("info_missing.json")
    with pytest.raises(WatchError, match=r"There is no page called .Zzyzx not a real page 7731"):
        await watchlist.add("enwiki", "Zzyzx not a real page 7731")
    assert watchlist.pages() == []


@respx.mock
async def test_adding_an_invalid_title_is_an_error(watchlist: Watchlist) -> None:
    respx.get(API).mock(return_value=action_response({"title": "Bad", "invalid": True, "invalidreason": "x"}))
    with pytest.raises(WatchError, match="There is no page"):
        await watchlist.add("enwiki", "Bad")


@respx.mock
async def test_adding_something_that_is_not_an_article_is_an_error(watchlist: Watchlist) -> None:
    respx.get(API).mock(return_value=action_response(action_page(5, "Talk:Ada Lovelace", 10, ns=1)))
    with pytest.raises(WatchError, match="not an article"):
        await watchlist.add("enwiki", "Talk:Ada Lovelace")


@respx.mock
async def test_add_failures_are_described_in_plain_words(watchlist: Watchlist) -> None:
    route = respx.get(API).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(WatchError, match="could not be reached"):
        await watchlist.add("enwiki", "Ada Lovelace")
    route.mock(return_value=httpx.Response(429, headers={"Retry-After": "20"}))
    with pytest.raises(WatchError, match=r"slow down.*20 seconds"):
        await watchlist.add("enwiki", "Ada Lovelace")
    route.mock(return_value=httpx.Response(404))
    with pytest.raises(WatchError, match="did not answer with a page"):
        await watchlist.add("enwiki", "Ada Lovelace")
    route.mock(return_value=httpx.Response(200, json={"query": {}}))
    with pytest.raises(WatchError, match="did not answer with a page"):
        await watchlist.add("enwiki", "Ada Lovelace")
    route.mock(return_value=httpx.Response(200, content=b"<html>"))
    with pytest.raises(WatchError, match="could not read"):
        await watchlist.add("enwiki", "Ada Lovelace")


async def test_add_on_an_unknown_wiki_makes_no_request(watchlist: Watchlist) -> None:
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(WatchError, match="cannot watch pages on commonswiki"):
            await watchlist.add("commonswiki", "Ada")
        assert not router.calls


def test_api_url() -> None:
    assert api_url("enwiki") == API
    assert api_url("be_x_oldwiki") == "https://be-x-old.wikipedia.org/w/api.php"
    assert api_url("frwiktionary") == "https://fr.wiktionary.org/w/api.php"
    with pytest.raises(ValueError, match="commonswiki"):
        api_url("commonswiki")


@respx.mock
async def test_match_needs_no_request_and_normalizes_the_title(watchlist: Watchlist) -> None:
    serve("info_existing.json")
    page = (await watchlist.add("enwiki", "Ada Lovelace")).page
    respx.reset()  # from here any request would fail the test: no route is mocked
    assert watchlist.match("enwiki", "Ada Lovelace") == page
    assert watchlist.match("enwiki", "Ada_Lovelace") == page
    assert watchlist.match("enwiki", " ada Lovelace ") == page
    assert watchlist.match("enwiki", "Ada lovelace") is None  # titles are case sensitive after the first letter
    assert watchlist.match("dewiki", "Ada Lovelace") is None
    assert not respx.calls


@respx.mock
async def test_wiktionary_titles_keep_their_case(watchlist: Watchlist) -> None:
    respx.get("https://fr.wiktionary.org/w/api.php").mock(return_value=action_response(action_page(77, "chat", 5)))
    page = (await watchlist.add("frwiktionary", "chat")).page
    assert watchlist.match("frwiktionary", "chat") == page
    assert watchlist.match("frwiktionary", "Chat") is None


@respx.mock
async def test_the_list_is_one_list_across_wikis(watchlist: Watchlist, clock: VirtualClock) -> None:
    serve("info_existing.json")
    respx.get("https://de.wikipedia.org/w/api.php").mock(return_value=action_response(action_page(9, "Alan Turing", 4)))
    await watchlist.add("enwiki", "Ada Lovelace")
    clock.advance(5)
    await watchlist.add("dewiki", "Alan Turing")
    assert [(p.wiki, p.title) for p in watchlist.pages()] == [("enwiki", "Ada Lovelace"), ("dewiki", "Alan Turing")]


@respx.mock
async def test_remove(watchlist: Watchlist, store: Store) -> None:
    serve("info_existing.json")
    page = (await watchlist.add("enwiki", "Ada Lovelace")).page
    assert watchlist.remove(page)
    assert watchlist.match("enwiki", "Ada Lovelace") is None
    assert watchlist.pages() == [] and store.watched_pages() == []
    assert not watchlist.remove(page)


@respx.mock
async def test_the_list_survives_a_restart(store: Store, client: Client, clock: VirtualClock) -> None:
    serve("info_existing.json")
    first = Watchlist(store, client, clock)
    page = (await first.add("enwiki", "Ada Lovelace")).page
    first.record_edit(make_edit())
    second = Watchlist(store, client, clock)
    assert second.match("enwiki", "Ada Lovelace") == first.match("enwiki", "Ada Lovelace")
    assert second.pages()[0].unseen == 1 and second.pages()[0].page_id == page.page_id


# --- live edits -------------------------------------------------------------------------------------------------------


@respx.mock
async def test_live_edits_count_as_unseen_and_move_the_newest_revision(watchlist: Watchlist) -> None:
    serve("info_existing.json")
    await watchlist.add("enwiki", "Ada Lovelace")
    first = watchlist.record_edit(make_edit(rev_new=ADA_LATEST + 1))
    assert first is not None and (first.unseen, first.last_rev_id) == (1, ADA_LATEST + 1)
    assert first.seen_rev_id == ADA_LATEST
    assert first.last_edit_at == make_edit().at
    second = watchlist.record_edit(make_edit(title="Ada_Lovelace", rev_new=ADA_LATEST + 5))
    assert second is not None and second.unseen == 2
    assert watchlist.unseen_total() == 2


@respx.mock
async def test_live_edits_that_do_not_count(watchlist: Watchlist) -> None:
    serve("info_existing.json")
    await watchlist.add("enwiki", "Ada Lovelace")
    assert watchlist.record_edit(make_edit(title="Alan Turing")) is None  # not watched
    assert watchlist.record_edit(make_edit(wiki="dewiki")) is None  # another wiki
    assert watchlist.record_edit(make_edit(rev_new=None)) is None  # no revision to compare
    assert watchlist.record_edit(make_edit(rev_new=ADA_LATEST)) is None  # already known (the same edit seen twice)
    assert watchlist.record_edit(make_edit(rev_new=ADA_LATEST - 10)) is None
    assert watchlist.unseen_total() == 0


@respx.mock
async def test_mark_seen_clears_the_count_and_moves_the_seen_revision(watchlist: Watchlist, store: Store) -> None:
    serve("info_existing.json")
    page = (await watchlist.add("enwiki", "Ada Lovelace")).page
    watchlist.record_edit(make_edit(rev_new=ADA_LATEST + 1))
    watchlist.record_edit(make_edit(rev_new=ADA_LATEST + 2))
    seen = watchlist.mark_seen(page)  # an older copy of the page is fine
    assert (seen.unseen, seen.seen_rev_id, seen.last_rev_id) == (0, ADA_LATEST + 2, ADA_LATEST + 2)
    assert watchlist.unseen_total() == 0
    assert store.watched_pages() == [seen]
    after = watchlist.record_edit(make_edit(rev_new=ADA_LATEST + 3))
    assert after is not None and (after.unseen, after.seen_rev_id) == (1, ADA_LATEST + 2)


# --- moves ------------------------------------------------------------------------------------------------------------


@respx.mock
async def test_a_move_keeps_the_page_under_its_new_title(watchlist: Watchlist, store: Store) -> None:
    serve("info_existing.json")
    page = (await watchlist.add("enwiki", "Ada Lovelace")).page
    moved = watchlist.apply_move("enwiki", "Ada_Lovelace", "Ada King, Countess of Lovelace")
    assert moved is not None and moved.title == "Ada King, Countess of Lovelace" and moved.page_id == page.page_id
    assert watchlist.match("enwiki", "Ada Lovelace") is None
    assert watchlist.match("enwiki", "Ada King, Countess of Lovelace") == moved
    assert [p.title for p in store.watched_pages()] == ["Ada King, Countess of Lovelace"]
    # later edits arrive under the new title
    edit = watchlist.record_edit(make_edit(title="Ada King, Countess of Lovelace"))
    assert edit is not None and edit.unseen == 1


@respx.mock
async def test_a_move_of_an_unwatched_page_does_nothing(watchlist: Watchlist) -> None:
    serve("info_existing.json")
    await watchlist.add("enwiki", "Ada Lovelace")
    assert watchlist.apply_move("enwiki", "Alan Turing", "Alan M. Turing") is None
    assert watchlist.apply_move("dewiki", "Ada Lovelace", "Ada") is None
    assert [p.title for p in watchlist.pages()] == ["Ada Lovelace"]
