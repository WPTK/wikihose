"""Startup catch-up: batching, the 30 day limit, renames, deleted pages and the request budget."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from payloads import ADA_ID, ADA_LATEST, ADA_PREVIOUS, TURING_ID, TURING_LATEST, action_page, fixture, make_edit

from wikihose.catchup import HISTORY_LIMIT, INFO_BATCH, LOOKBACK, MAX_HISTORY_REQUESTS, catch_up
from wikihose.clock import VirtualClock
from wikihose.net import Client, RequestBudget
from wikihose.store import Store
from wikihose.watchlist import Watchlist

EN = "https://en.wikipedia.org/w/api.php"
DE = "https://de.wikipedia.org/w/api.php"
HISTORY = fixture("actionapi/revisions_one.json")  # Ada Lovelace, newest 50 revisions, with a "continue"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "wikihose.db")
    yield db
    db.close()


def watch(store: Store, clock: VirtualClock, wiki: str, page_id: int, title: str, seen: int) -> None:
    store.add_watch(wiki, page_id, title, clock.now(), seen)
    clock.advance(1)  # a stable order: oldest added first


class Wiki:
    """A fake Action API: ``prop=info`` answers from ``info``, ``prop=revisions`` from ``history``."""

    def __init__(
        self, info: dict[int, dict[str, Any]], history: Callable[[httpx.Request], httpx.Response] | None = None
    ):
        self.info = info
        self.history = history or self._ada_history
        self.requests: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        if "titles" in params:  # adding a page
            return httpx.Response(
                200, json={"query": {"pages": [p for p in self.info.values() if p["title"] == params["titles"]]}}
            )
        if params["prop"] == "info":
            ids = [int(i) for i in params["pageids"].split("|")]
            pages = [self.info.get(i, {"pageid": i, "missing": True}) for i in ids]
            return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": pages}})
        return self.history(request)

    @staticmethod
    def _ada_history(request: httpx.Request) -> httpx.Response:
        """The saved history, cut at ``rvend`` the way MediaWiki does, with no ``continue`` once it is cut."""
        end = request.url.params["rvend"]
        revisions = [r for r in HISTORY["query"]["pages"][0]["revisions"] if r["timestamp"] >= end]
        page = {**HISTORY["query"]["pages"][0], "revisions": revisions}
        body: dict[str, Any] = {"batchcomplete": True, "query": {"pages": [page]}}
        if len(revisions) == HISTORY_LIMIT:
            body["continue"] = HISTORY["continue"]
        return httpx.Response(200, json=body)

    def of(self, prop: str) -> list[dict[str, str]]:
        return [r for r in self.requests if r["prop"] == prop]


BATCH = {
    page["pageid"]: page for page in fixture("actionapi/info_batch.json")["query"]["pages"]
}  # real; page 2 is gone
ADA_INFO = {ADA_ID: BATCH[ADA_ID]}
TURING_INFO = {TURING_ID: BATCH[TURING_ID]}


@respx.mock
async def test_a_changed_page_gets_its_unseen_edits_and_a_quiet_page_costs_nothing_extra(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_PREVIOUS)
    watch(store, clock, "enwiki", TURING_ID, "Alan Turing", TURING_LATEST)
    watchlist = Watchlist(store, client, clock)
    wiki = Wiki({**ADA_INFO, **TURING_INFO})
    respx.get(EN).mock(side_effect=wiki)

    result = await catch_up(watchlist, client, clock)

    assert len(wiki.of("info")) == 1 and len(wiki.of("revisions")) == 1  # both pages in one info request
    assert wiki.of("info")[0]["pageids"] == f"{ADA_ID}|{TURING_ID}"
    (edit,) = result.edits
    assert (edit.rev_id, edit.parent_id, edit.user, edit.size, edit.delta) == (
        ADA_LATEST,
        ADA_PREVIOUS,
        "Vitorperrut555",
        98391,
        41,
    )
    assert edit.title == "Ada Lovelace" and edit.at.isoformat() == "2026-11-06T19:02:23+00:00"
    assert "Cat-a-lot" in edit.comment
    ada = watchlist.match("enwiki", "Ada Lovelace")
    assert ada is not None and (ada.unseen, ada.last_rev_id, ada.seen_rev_id) == (1, ADA_LATEST, ADA_PREVIOUS)
    assert ada.last_edit_at == edit.at
    turing = watchlist.match("enwiki", "Alan Turing")
    assert turing is not None and turing.unseen == 0
    assert watchlist.unseen_total() == 1
    assert store.watched_pages()[0].unseen == 1  # saved
    assert (result.renamed, result.deleted, result.errors) == ([], [], [])


@respx.mock
async def test_the_history_request_asks_for_one_page_and_stops_thirty_days_back(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", 1)  # last seen long ago
    wiki = Wiki(ADA_INFO)
    respx.get(EN).mock(side_effect=wiki)
    watchlist = Watchlist(store, client, clock)

    result = await catch_up(watchlist, client, clock)

    (request,) = wiki.of("revisions")
    assert request["pageids"] == str(ADA_ID)  # never several pages: MediaWiki refuses rvlimit with more than one
    assert request["rvlimit"] == str(HISTORY_LIMIT)
    assert request["rvprop"] == "ids|timestamp|user|comment|size"
    assert request["rvend"] == (clock.now() - LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-10-08T12:00:01Z"
    assert (request["format"], request["formatversion"]) == ("json", "2")
    # only the revisions inside the window came back, newest first; the 45 older ones are not reported
    assert [e.rev_id for e in result.edits] == [ADA_LATEST, ADA_PREVIOUS, 1381003492, 1380448378, 1379797630]
    assert result.edits[-1].delta is None  # its parent is outside the window
    assert watchlist.match("enwiki", "Ada Lovelace") is not None


@respx.mock
async def test_a_page_changed_only_before_the_window_still_counts_as_changed(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", 1)
    clock.advance(60 * 86400)  # sixty days on: every revision in the saved history is older than the window
    wiki = Wiki(ADA_INFO)
    respx.get(EN).mock(side_effect=wiki)
    watchlist = Watchlist(store, client, clock)

    result = await catch_up(watchlist, client, clock)

    assert result.edits == []
    ada = watchlist.match("enwiki", "Ada Lovelace")
    assert ada is not None and (ada.unseen, ada.last_rev_id) == (1, ADA_LATEST)


@respx.mock
async def test_history_follows_continue_up_to_the_limit(store: Store, client: Client, clock: VirtualClock) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", 0)
    calls: list[dict[str, str]] = []

    def endless(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        n = len(calls)
        revisions = [
            {"revid": 10_000 - 50 * n - i, "parentid": 0, "user": "U", "timestamp": "2026-11-06T00:00:00Z", "size": 1}
            for i in range(HISTORY_LIMIT)
        ]
        page = {"pageid": ADA_ID, "ns": 0, "title": "Ada Lovelace", "revisions": revisions}
        return httpx.Response(
            200, json={"query": {"pages": [page]}, "continue": {"rvcontinue": f"x{n}", "continue": "||"}}
        )

    respx.get(EN).mock(side_effect=Wiki(ADA_INFO, endless))
    watchlist = Watchlist(store, client, clock)

    result = await catch_up(watchlist, client, clock)

    assert len(calls) == MAX_HISTORY_REQUESTS
    assert [c.get("rvcontinue") for c in calls] == [None, "x1", "x2", "x3"]
    assert len(result.edits) == MAX_HISTORY_REQUESTS * HISTORY_LIMIT
    ada = watchlist.match("enwiki", "Ada Lovelace")
    assert ada is not None and ada.unseen == MAX_HISTORY_REQUESTS * HISTORY_LIMIT


@respx.mock
async def test_history_stops_paging_at_the_revision_last_seen(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", 1380448378)
    wiki = Wiki(ADA_INFO)
    respx.get(EN).mock(side_effect=wiki)

    def full_page(request: httpx.Request) -> httpx.Response:
        """50 revisions and a ``continue``, as MediaWiki sends when more are available."""
        return httpx.Response(200, json={**HISTORY, "continue": {"rvcontinue": "more", "continue": "||"}})

    wiki.history = full_page
    result = await catch_up(Watchlist(store, client, clock), client, clock)
    assert len(wiki.of("revisions")) == 1  # the page that held the last seen revision was enough
    assert [e.rev_id for e in result.edits] == [ADA_LATEST, ADA_PREVIOUS, 1381003492]
    assert result.edits[-1].delta == 98154 - 98137  # 1381003492 against its parent, 1380448378


@respx.mock
async def test_a_renamed_page_is_found_by_its_page_id(store: Store, client: Client, clock: VirtualClock) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_LATEST)
    respx.get(EN).mock(side_effect=Wiki({ADA_ID: action_page(ADA_ID, "Ada King, Countess of Lovelace", ADA_LATEST)}))
    watchlist = Watchlist(store, client, clock)

    result = await catch_up(watchlist, client, clock)

    ((old, page),) = result.renamed
    assert old == "Ada Lovelace" and page.title == "Ada King, Countess of Lovelace" and page.page_id == ADA_ID
    assert watchlist.match("enwiki", "Ada Lovelace") is None
    assert watchlist.match("enwiki", "Ada King, Countess of Lovelace") == page
    assert store.watched_pages()[0].title == "Ada King, Countess of Lovelace"


@respx.mock
async def test_a_deleted_page_is_marked_once_and_restored_when_it_returns(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_LATEST)
    wiki = Wiki({})  # the page is gone
    respx.get(EN).mock(side_effect=wiki)
    watchlist = Watchlist(store, client, clock)

    first = await catch_up(watchlist, client, clock)
    assert [p.page_id for p in first.deleted] == [ADA_ID]
    page = watchlist.match("enwiki", "Ada Lovelace")
    assert page is not None and page.deleted
    assert len(wiki.of("revisions")) == 0

    second = await catch_up(watchlist, client, clock)
    assert second.deleted == []  # already known

    wiki.info = ADA_INFO  # undeleted
    third = await catch_up(watchlist, client, clock)
    restored = watchlist.match("enwiki", "Ada Lovelace")
    assert third.deleted == [] and restored is not None and not restored.deleted
    assert not store.watched_pages()[0].deleted


@respx.mock
async def test_pages_are_batched_by_wiki_in_groups_of_fifty(store: Store, client: Client, clock: VirtualClock) -> None:
    for page_id in range(1, 121):
        watch(store, clock, "enwiki", page_id, f"Page {page_id}", 5)
    watch(store, clock, "dewiki", 500, "Seite", 5)
    en = Wiki({i: action_page(i, f"Page {i}", 5) for i in range(1, 121)})
    de = Wiki({500: action_page(500, "Seite", 5)})
    respx.get(EN).mock(side_effect=en)
    respx.get(DE).mock(side_effect=de)
    seen: list[tuple[int, int]] = []

    result = await catch_up(
        Watchlist(store, client, clock), client, clock, lambda done, total: seen.append((done, total))
    )

    assert [len(r["pageids"].split("|")) for r in en.of("info")] == [INFO_BATCH, INFO_BATCH, 20]
    assert len(de.of("info")) == 1
    assert en.of("revisions") == [] and result.edits == []  # nothing changed, so nothing more was asked
    assert seen[0] == (0, 121) and seen[-1] == (121, 121) and len(seen) == 122
    assert seen == sorted(seen)


@respx.mock
async def test_the_request_budget_applies_to_the_catch_up(store: Store, clock: VirtualClock) -> None:
    for page_id in range(1, 121):
        watch(store, clock, "enwiki", page_id, f"Page {page_id}", 5)
    respx.get(EN).mock(side_effect=Wiki({i: action_page(i, f"Page {i}", 5) for i in range(1, 121)}))
    client = Client(clock, RequestBudget(clock, per_minute=2))
    try:
        start = clock.monotonic()
        await catch_up(Watchlist(store, client, clock), client, clock)
        assert clock.monotonic() - start == 60.0  # the third request waited for the window; nothing really slept
    finally:
        await client.aclose()


@respx.mock
async def test_a_failed_request_is_reported_and_the_rest_carries_on(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "dewiki", 500, "Seite", 5)
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_PREVIOUS)
    respx.get(DE).mock(side_effect=httpx.ConnectError("no route"))
    respx.get(EN).mock(side_effect=Wiki(ADA_INFO))
    watchlist = Watchlist(store, client, clock)
    seen: list[tuple[int, int]] = []

    result = await catch_up(watchlist, client, clock, lambda done, total: seen.append((done, total)))

    assert result.errors == ["Wikipedia could not be reached. Check your connection and try again."]
    assert len(result.edits) == 1
    assert seen[-1] == (2, 2)
    seite = watchlist.match("dewiki", "Seite")
    assert seite is not None and not seite.deleted  # an unreachable wiki does not mean the page is gone


@respx.mock
async def test_a_failed_history_request_names_the_page(store: Store, client: Client, clock: VirtualClock) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_PREVIOUS)
    respx.get(EN).mock(side_effect=Wiki(ADA_INFO, lambda request: httpx.Response(400)))
    result = await catch_up(Watchlist(store, client, clock), client, clock)
    assert result.errors == ["Ada Lovelace: Wikipedia sent an answer wikihose could not read."]


@respx.mock
async def test_an_answer_in_the_wrong_shape_is_an_error_not_a_crash(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_PREVIOUS)
    route = respx.get(EN).mock(return_value=httpx.Response(200, json={"oops": 1}))
    result = await catch_up(Watchlist(store, client, clock), client, clock)
    assert result.errors == ["Wikipedia sent an answer wikihose could not read."]
    route.mock(side_effect=Wiki(ADA_INFO, lambda request: httpx.Response(200, json={"query": {"pages": []}})))
    result = await catch_up(Watchlist(store, client, clock), client, clock)
    assert result.errors and "Ada Lovelace" in result.errors[0]


@respx.mock
async def test_an_edit_seen_live_and_again_in_the_catch_up_counts_once(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", ADA_PREVIOUS)
    respx.get(EN).mock(side_effect=Wiki(ADA_INFO))
    watchlist = Watchlist(store, client, clock)
    watchlist.record_edit(make_edit(rev_new=ADA_LATEST))  # the live stream got there first

    await catch_up(watchlist, client, clock)
    ada = watchlist.match("enwiki", "Ada Lovelace")
    assert ada is not None and ada.unseen == 1

    assert watchlist.record_edit(make_edit(rev_new=ADA_LATEST)) is None  # and a late duplicate is ignored
    assert watchlist.unseen_total() == 1


@respx.mock
async def test_a_page_added_just_now_has_nothing_to_catch_up(store: Store, client: Client, clock: VirtualClock) -> None:
    wiki = Wiki(ADA_INFO)
    respx.get(EN).mock(side_effect=wiki)
    watchlist = Watchlist(store, client, clock)
    await watchlist.add("enwiki", "Ada Lovelace")
    wiki.requests.clear()

    result = await catch_up(watchlist, client, clock)

    assert result.edits == [] and watchlist.unseen_total() == 0
    assert len(wiki.requests) == 1 and wiki.requests[0]["prop"] == "info"


@respx.mock
async def test_hidden_users_and_comments_are_not_shown(store: Store, client: Client, clock: VirtualClock) -> None:
    watch(store, clock, "enwiki", ADA_ID, "Ada Lovelace", 1)

    def hidden(request: httpx.Request) -> httpx.Response:
        revision = {"revid": 7, "parentid": 0, "timestamp": "2026-11-06T00:00:00Z", "size": 3, "userhidden": True,
                    "commenthidden": True}  # fmt: skip
        page = {"pageid": ADA_ID, "ns": 0, "title": "Ada Lovelace", "revisions": [revision]}
        return httpx.Response(200, json={"query": {"pages": [page]}})

    respx.get(EN).mock(side_effect=Wiki(ADA_INFO, hidden))
    result = await catch_up(Watchlist(store, client, clock), client, clock)
    assert [(e.user, e.comment, e.delta) for e in result.edits] == [("(hidden)", "(hidden)", 3)]


def test_the_lookback_is_thirty_days() -> None:
    assert timedelta(days=30) == LOOKBACK


def _revision(clock: VirtualClock, rev: int, parent: int, size: int) -> dict[str, Any]:
    return {
        "revid": rev,
        "parentid": parent,
        "timestamp": clock.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": "Someone",
        "comment": "",
        "size": size,
    }


@respx.mock
async def test_a_live_edit_that_arrives_during_the_history_request_counts_once(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", 974, "Page 974", 100)
    watchlist = Watchlist(store, client, clock)

    def history(request: httpx.Request) -> httpx.Response:
        watchlist.record_edit(make_edit(title="Page 974", rev_new=101))  # the stream gets 101 while we wait
        revisions = [_revision(clock, 102, 101, 30), _revision(clock, 101, 100, 20)]
        return httpx.Response(200, json={"query": {"pages": [{"pageid": 974, "revisions": revisions}]}})

    respx.get(EN).mock(side_effect=Wiki({974: action_page(974, "Page 974", 102)}, history))
    result = await catch_up(watchlist, client, clock)
    page = watchlist.match("enwiki", "Page 974")
    assert page is not None and (page.unseen, page.last_rev_id) == (2, 102)
    assert [edit.rev_id for edit in result.edits] == [102]


@respx.mock
async def test_nothing_is_added_when_the_live_stream_already_counted_every_new_revision(
    store: Store, client: Client, clock: VirtualClock
) -> None:
    watch(store, clock, "enwiki", 974, "Page 974", 100)
    watchlist = Watchlist(store, client, clock)

    def history(request: httpx.Request) -> httpx.Response:
        watchlist.record_edit(make_edit(title="Page 974", rev_new=101))
        revisions = [_revision(clock, 101, 100, 20)]
        return httpx.Response(200, json={"query": {"pages": [{"pageid": 974, "revisions": revisions}]}})

    respx.get(EN).mock(side_effect=Wiki({974: action_page(974, "Page 974", 101)}, history))
    await catch_up(watchlist, client, clock)
    page = watchlist.match("enwiki", "Page 974")
    assert page is not None and page.unseen == 1
