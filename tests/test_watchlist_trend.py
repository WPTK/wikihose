"""The Watchlist tab's Trend column: fetched when the tab is shown, cached, and quiet when it cannot be."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from payloads import ADA_ID, ADA_PREVIOUS, TURING_ID, TURING_LATEST
from support import ListSource, action_api, finish_work, make_app
from textual.widgets import DataTable, Static

from wikihose.clock import VirtualClock
from wikihose.net import Client
from wikihose.store import Store
from wikihose.trend import PAUSE
from wikihose.ui.app import WikihoseApp
from wikihose.ui.tabs.watchlist import WatchlistTab

SIZE = (100, 30)
ADA_URL = r".*/metrics/pageviews/per-article/en.wikipedia/all-access/user/Ada_Lovelace/daily/20261008/20261106"
FIRST = date(2026, 10, 8)  # the window for a clock at 2026-11-07 UTC: 30 days ending yesterday


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store(tmp_path / "wikihose.db")
    yield db
    db.close()


def payload(views: dict[int, int]) -> dict[str, Any]:
    items = [
        {"timestamp": f"{FIRST + timedelta(days=offset):%Y%m%d}00", "views": count} for offset, count in views.items()
    ]
    return {"items": items}


def climbing() -> dict[str, Any]:
    return payload({n: 100 * (n + 1) for n in range(29)})  # the newest day (offset 29) is not published yet


def app_for(clock: VirtualClock, store: Store, client: Client | None) -> WikihoseApp:
    store.add_watch("enwiki", ADA_ID, "Ada Lovelace", clock.now(), ADA_PREVIOUS)
    return make_app(ListSource(clock, []), clock, store=store, client=client)


def trend_cell(app: WikihoseApp) -> str:
    return str(app.query_one("#watch-table", DataTable).get_row_at(0)[5])


async def show_tab(app: WikihoseApp, pilot: Any) -> None:
    await pilot.press("3")
    await finish_work(app, pilot)


@respx.mock
async def test_the_trend_column_fills_in_from_the_pageview_history(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    route = respx.get(url__regex=ADA_URL).mock(return_value=httpx.Response(200, json=climbing()))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        cell = trend_cell(app)
        assert len(cell) == 8 and cell[0] == "▁" and cell[-1] == "█"
        assert not app.query_one("#watch-status", Static).display
    assert route.call_count == 1
    assert "agent" not in str(route.calls[0].request.url) and "/user/" in str(route.calls[0].request.url)


@respx.mock
async def test_showing_the_tab_again_and_restarting_do_not_fetch_again(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    route = respx.get(url__regex=ADA_URL).mock(return_value=httpx.Response(200, json=climbing()))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        await pilot.press("1")
        await show_tab(app, pilot)
    async with make_app(ListSource(clock, []), clock, store=store, client=client).run_test(size=SIZE) as pilot:
        app2 = pilot.app
        await show_tab(app2, pilot)
        assert trend_cell(app2)[-1] == "█"  # from the pageviews table
    assert route.call_count == 1
    stored = store.pageviews("enwiki", "Ada_Lovelace", "2026-10-08", "2026-11-06")
    assert len(stored) == 29 and stored["2026-10-08"] == 100 and "2026-11-06" not in stored


@respx.mock
async def test_the_next_day_fetches_again(clock: VirtualClock, store: Store, client: Client) -> None:
    route = respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(200, json=climbing()))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        await pilot.press("1")
        await show_tab(app, pilot)  # same session: the column is already filled
    assert route.call_count == 1
    clock.advance(86400)
    async with make_app(ListSource(clock, []), clock, store=store, client=client).run_test(size=SIZE) as pilot:
        await show_tab(pilot.app, pilot)
    assert route.call_count == 2


@respx.mock
async def test_a_page_with_no_pageview_data_shows_a_dash(clock: VirtualClock, store: Store, client: Client) -> None:
    route = respx.get(url__regex=ADA_URL).mock(return_value=httpx.Response(404))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        assert trend_cell(app) == "-"
        assert not app.query_one("#watch-status", Static).display
    assert route.call_count == 1


@respx.mock
async def test_the_cell_shows_a_dot_while_the_history_is_loading(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=climbing())

    respx.get(url__regex=ADA_URL).mock(side_effect=slow)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert trend_cell(app) == "·"
        gate.set()
        await finish_work(app, pilot)
        assert trend_cell(app)[-1] == "█"


@respx.mock
async def test_a_failed_request_says_so_in_words_and_leaves_a_dash(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    respx.get(url__regex=ADA_URL).mock(side_effect=httpx.ConnectError("no route"))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        status = app.query_one("#watch-status", Static)
        assert status.display
        assert "could not reach Wikimedia" in str(status.render())
        assert trend_cell(app) == "-"


@respx.mock
async def test_a_rate_limited_request_says_when_it_will_retry(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    respx.get(url__regex=ADA_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        assert "rate limited, retrying in 30s" in str(app.query_one("#watch-status", Static).render())
        assert trend_cell(app) == "-"


@respx.mock
async def test_a_page_on_a_wiki_without_pageviews_is_skipped_quietly(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    route = respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(200, json=climbing()))
    store.add_watch("dewiktionary", 7, "Haus", clock.now(), 1)
    store.add_watch("commonswiki", 8, "File:X.jpg", clock.now(), 1)
    app = make_app(ListSource(clock, []), clock, store=store, client=client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        rows = app.query_one("#watch-table", DataTable)
        assert [str(rows.get_row_at(i)[5]) for i in range(rows.row_count)] == ["-", "-"]
        assert not app.query_one("#watch-status", Static).display
    assert route.call_count == 0


@respx.mock
async def test_a_replay_makes_no_pageview_requests(clock: VirtualClock, store: Store) -> None:
    route = respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(200, json=climbing()))
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        assert trend_cell(app) == "-"
    assert not route.called


@respx.mock
async def test_the_narrow_layout_has_no_trend_column_and_asks_for_nothing(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    route = respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(200, json=climbing()))
    app = app_for(clock, store, client)
    async with app.run_test(size=(80, 24)) as pilot:
        await show_tab(app, pilot)
        headers = [str(column.label) for column in app.query_one("#watch-table", DataTable).columns.values()]
        assert "Trend" not in headers and "Page" in headers
    assert not route.called


def stored_trend(store: Store, title: str, wiki: str = "enwiki") -> None:
    """A fresh trend already in the pageviews table (a fetch made today)."""
    days = {f"{FIRST + timedelta(days=n):%Y-%m-%d}": 10 * (n + 1) for n in range(29)}
    store.save_pageviews(wiki, title, days)


@respx.mock
async def test_only_requests_pause_and_fresh_pages_draw_at_once(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    for number, title in enumerate(["Fresh_One", "Fresh_Two"]):
        store.add_watch("enwiki", 100 + number, title.replace("_", " "), clock.now(), 1)
        stored_trend(store, title)
    store.add_watch("enwiki", 200, "Net One", clock.now(), 1)
    store.add_watch("enwiki", 201, "Net Two", clock.now(), 1)
    order: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        order.append(str(request.url).split("/user/")[1].split("/")[0])
        return httpx.Response(200, json=climbing())

    route = respx.get(url__regex=r".*/per-article/.*").mock(side_effect=answer)
    app = make_app(ListSource(clock, []), clock, store=store, client=client)
    async with app.run_test(size=SIZE) as pilot:
        before = clock.now()
        await show_tab(app, pilot)
        rows = app.query_one("#watch-table", DataTable)
        assert all(str(rows.get_row_at(i)[5])[-1] == "█" for i in range(4))
        assert clock.now() - before == timedelta(seconds=PAUSE)  # two requests, one wait between them
    assert order == ["Net_One", "Net_Two"] and route.call_count == 2  # one at a time, fresh pages asked for nothing


@respx.mock
async def test_a_rate_limited_fill_retries_after_the_delay(clock: VirtualClock, store: Store, client: Client) -> None:
    calls = 0

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 3:  # the client's three tries
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=climbing())

    respx.get(url__regex=ADA_URL).mock(side_effect=answer)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        assert trend_cell(app) == "-" and "rate limited" in str(app.query_one("#watch-status", Static).render())
        await asyncio.sleep(1.3)
        await finish_work(app, pilot)
        assert trend_cell(app)[-1] == "█"
        assert not app.query_one("#watch-status", Static).display


@respx.mock
async def test_a_page_added_while_the_tab_is_shown_gets_a_trend(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(200, json=climbing()))
    action_api((TURING_ID, "Alan Turing", TURING_LATEST))
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        await app.watchlist.add("enwiki", "Alan Turing")
        await finish_work(app, pilot)
        rows = app.query_one("#watch-table", DataTable)
        assert rows.row_count == 2 and str(rows.get_row_at(1)[5])[-1] == "█"


@respx.mock
async def test_a_replay_shows_a_trend_that_is_already_stored(clock: VirtualClock, store: Store) -> None:
    route = respx.get(url__regex=r".*/per-article/.*").mock(return_value=httpx.Response(404))
    stored_trend(store, "Ada_Lovelace")
    app = app_for(clock, store, None)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        assert trend_cell(app)[-1] == "█"
    assert not route.called


@respx.mock
async def test_showing_the_tab_again_does_not_cancel_a_fill_in_progress(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    gate = asyncio.Event()
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await gate.wait()
        return httpx.Response(200, json=climbing())

    respx.get(url__regex=ADA_URL).mock(side_effect=slow)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        app.query_one(WatchlistTab).on_show()  # a second show while the request is in flight
        await pilot.resize_terminal(110, 30)
        await pilot.pause()
        gate.set()
        await finish_work(app, pilot)
        assert trend_cell(app)[-1] == "█"
    assert calls == 1


@respx.mock
async def test_leaving_the_tab_cancels_the_fill(clock: VirtualClock, store: Store, client: Client) -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=climbing())

    respx.get(url__regex=ADA_URL).mock(side_effect=slow)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        await pilot.press("1")
        await finish_work(app, pilot)
        assert not any(w.group == "trend" and not w.is_finished for w in app.workers)
        assert app.query_one(WatchlistTab)._series == {}


@respx.mock
async def test_an_error_arriving_while_the_app_quits_raises_nothing(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        raise httpx.ConnectError("gone")

    respx.get(url__regex=ADA_URL).mock(side_effect=slow)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("3")
        await pilot.pause()
        gate.set()
        app.exit()
        await pilot.pause()
    assert not any(w.group == "trend" and not w.is_finished for w in app.workers)


@respx.mock
async def test_a_page_with_unreadable_data_keeps_its_message_until_the_next_fill(
    clock: VirtualClock, store: Store, client: Client
) -> None:
    store.add_watch("enwiki", 300, "Odd", clock.now(), 1)

    def answer(request: httpx.Request) -> httpx.Response:
        if "/Odd/" in str(request.url):
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, json=climbing())

    respx.get(url__regex=r".*/per-article/.*").mock(side_effect=answer)
    app = app_for(clock, store, client)
    async with app.run_test(size=SIZE) as pilot:
        await show_tab(app, pilot)
        assert "unexpected for Odd" in str(app.query_one("#watch-status", Static).render())
        rows = app.query_one("#watch-table", DataTable)
        cells = {str(rows.get_row_at(i)[1]).strip(): str(rows.get_row_at(i)[5])[-1] for i in range(2)}
        assert cells == {"Ada Lovelace": "█", "Odd": "-"}
