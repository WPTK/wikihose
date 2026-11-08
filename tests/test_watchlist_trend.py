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
from payloads import ADA_ID, ADA_PREVIOUS
from support import ListSource, finish_work, make_app
from textual.widgets import DataTable, Static

from wikihose.clock import VirtualClock
from wikihose.net import Client
from wikihose.store import Store
from wikihose.ui.app import WikihoseApp

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
    assert not route.called
