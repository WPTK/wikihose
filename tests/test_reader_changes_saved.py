"""What the reader does is saved in every mode; what a recording holds never is.

Each test makes a change, closes the app, opens the files again as the next run would and checks the change is there.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from payloads import ADA_ID, ADA_LATEST, make_edit
from support import ListSource, action_api, finish_work, make_app, settle, wait_for_stream

from wikihose.clock import VirtualClock
from wikihose.config import Config, load_config
from wikihose.net import Client
from wikihose.sources.recording import sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.store import Store
from wikihose.ui.tabs.live import LiveTab
from wikihose.ui.tabs.reading import ReadingTab
from wikihose.watchlist import Watchlist, is_placeholder

SIZE = (100, 30)
MODES = ["stream", "replay"]  # the demo is the replay plus a loop; its journey is in tests/realterm


@pytest.fixture(autouse=True)
def _no_pageviews() -> Any:
    with respx.mock:
        respx.get(url__regex=r".*/metrics/pageviews/per-article/.*").mock(return_value=httpx.Response(404))
        yield


def rows_in(db: Path, table: str) -> int:
    connection = sqlite3.connect(db)
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


async def drive(app: Any) -> None:
    """One run: watch a page, change filters, the Reading row count and the theme, mark the page seen, quit."""
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press("W")
        await pilot.pause()
        await pilot.press(*"Ada Lovelace", "enter")
        await finish_work(app, pilot)
        assert len(app.watchlist) == 1
        await pilot.press("b", "a", "s")  # bots shown, all pages, 100 bytes
        app.query_one(ReadingTab).action_more_rows()  # 50 rows
        app.theme = "wikihose-light"
        await pilot.pause()
        await pilot.press("3", "m")
        await pilot.pause()


@pytest.mark.parametrize("mode", MODES)
@respx.mock
async def test_the_watchlist_and_the_settings_survive_a_restart(
    mode: str, clock: VirtualClock, client: Client, tmp_path: Path
) -> None:
    db = tmp_path / "data" / "wikihose.db"
    config_path = tmp_path / "config" / "config.toml"
    if mode == "stream":
        action_api((ADA_ID, "Ada Lovelace", ADA_LATEST))
    with sample_recording() as path:
        source: Any = ListSource(clock) if mode == "stream" else ReplaySource(path, clock)
        store = Store(db, history=mode == "stream")
        app = make_app(
            source,
            clock,
            store=store,
            config_path=config_path,
            client=client if mode == "stream" else None,
        )
        try:
            await drive(app)
        finally:
            store.close()

    saved, warnings = load_config(config_path)
    assert warnings == []
    assert (saved.hide_bots, saved.articles_only, saved.min_bytes) == (False, False, 100)
    assert (saved.reading_rows, saved.theme) == (50, "wikihose-light")
    again = Store(db)
    try:
        [page] = again.watched_pages()
        assert page.title == "Ada Lovelace"
        assert (page.page_id == ADA_ID) == (mode == "stream")  # offline it is a placeholder until a live start
        assert page.unseen == 0
    finally:
        again.close()


async def test_a_replay_flushes_its_counts_to_a_store_that_keeps_none(clock: VirtualClock, tmp_path: Path) -> None:
    db = tmp_path / "wikihose.db"
    store = Store(db, history=False)
    try:
        with sample_recording() as path:
            app = make_app(ReplaySource(path, clock), clock, store=store)
            async with app.run_test(size=SIZE) as pilot:
                await wait_for_stream(app, pilot)
                live = app.query_one(LiveTab)
                assert live._stats._pending  # there are counts to write
                live._stats.flush(live._store)  # what the once-a-minute write does
    finally:
        store.close()
    assert rows_in(db, "edit_minutes") == 0
    assert rows_in(db, "pageviews") == 0


async def test_a_live_run_still_records_its_edit_minutes(tmp_path: Path) -> None:
    store = Store(tmp_path / "wikihose.db")
    store.add_edit_minute("enwiki", "Ada Lovelace", "2026-11-07T14:05")
    store.save_pageviews("enwiki", "Ada_Lovelace", {"2026-11-06": 9})
    store.close()
    assert rows_in(tmp_path / "wikihose.db", "edit_minutes") == 1
    assert rows_in(tmp_path / "wikihose.db", "pageviews") == 1


def test_history_off_drops_recorded_data_but_keeps_the_watchlist(tmp_path: Path, clock: VirtualClock) -> None:
    db = tmp_path / "wikihose.db"
    store = Store(db, history=False)
    store.add_edit_minute("enwiki", "Ada Lovelace", "2026-11-07T14:05")
    store.save_pageviews("enwiki", "Ada_Lovelace", {"2026-11-06": 9})
    store.add_watch("enwiki", ADA_ID, "Ada Lovelace", clock.now(), 5)
    store.close()
    assert (rows_in(db, "edit_minutes"), rows_in(db, "pageviews"), rows_in(db, "watchlist")) == (0, 0, 1)


async def test_no_config_still_means_no_config_file_in_any_mode(clock: VirtualClock, tmp_path: Path) -> None:
    store = Store(tmp_path / "wikihose.db", history=False)
    try:
        with sample_recording() as path:
            app = make_app(ReplaySource(path, clock), clock, store=store, config_path=None)
            await drive(app)
    finally:
        store.close()
    assert not (tmp_path / "config").exists()
    assert Store(tmp_path / "wikihose.db").watched_pages()  # the watchlist is saved all the same


async def test_the_theme_at_startup_is_not_written_back(clock: VirtualClock, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    app = make_app(ListSource(clock), clock, config_path=config_path, config=Config(theme="wikihose-light"))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
    assert not config_path.exists()


# --- a page watched offline gets its real ID at the next live start ------------------------------------------------------


@respx.mock
async def test_a_placeholder_page_gets_its_real_id_when_there_is_a_connection(
    clock: VirtualClock, client: Client, tmp_path: Path
) -> None:
    store = Store(tmp_path / "wikihose.db")
    offline = Watchlist(store, None, clock)
    added = (await offline.add("enwiki", "ada lovelace")).page
    assert is_placeholder(added) and added.title == "Ada lovelace"
    action_api((ADA_ID, "Ada Lovelace", ADA_LATEST))
    online = Watchlist(store, client, clock)
    await online.resolve_placeholders()
    [page] = online.pages()
    assert (page.page_id, page.title, page.added_at) == (ADA_ID, "Ada Lovelace", added.added_at)
    assert (page.unseen, page.seen_rev_id) == (0, ADA_LATEST)
    assert [p.page_id for p in store.watched_pages()] == [ADA_ID]
    store.close()


@respx.mock
async def test_adding_a_page_online_replaces_its_placeholder(
    clock: VirtualClock, client: Client, tmp_path: Path
) -> None:
    store = Store(tmp_path / "wikihose.db")
    await Watchlist(store, None, clock).add("enwiki", "Ada Lovelace")
    action_api((ADA_ID, "Ada Lovelace", ADA_LATEST))
    online = Watchlist(store, client, clock)
    result = await online.add("enwiki", "Ada Lovelace")
    assert not result.already_watched and result.page.page_id == ADA_ID
    assert [p.page_id for p in store.watched_pages()] == [ADA_ID]
    store.close()


@respx.mock
async def test_a_placeholder_the_wiki_does_not_know_stays_and_is_not_asked_about_in_the_catch_up(
    clock: VirtualClock, client: Client, tmp_path: Path
) -> None:
    from wikihose.catchup import catch_up

    store = Store(tmp_path / "wikihose.db")
    await Watchlist(store, None, clock).add("enwiki", "No such page here")
    route = action_api()
    online = Watchlist(store, client, clock)
    await online.resolve_placeholders()
    assert [is_placeholder(p) for p in online.pages()] == [True]
    calls = route.call_count
    result = await catch_up(online, client, clock)
    assert route.call_count == calls and result.errors == []
    store.close()


async def test_an_offline_edit_to_a_placeholder_page_counts_as_unseen(clock: VirtualClock, tmp_path: Path) -> None:
    store = Store(tmp_path / "wikihose.db")
    watchlist = Watchlist(store, None, clock)
    await watchlist.add("enwiki", "Ada Lovelace")
    assert watchlist.record_edit(make_edit("Ada Lovelace", rev_new=7)) is not None
    assert watchlist.unseen_total() == 1
    store.close()


async def test_two_offline_pages_never_share_an_id_even_after_a_move(clock: VirtualClock, tmp_path: Path) -> None:
    store = Store(tmp_path / "wikihose.db")
    watchlist = Watchlist(store, None, clock)
    await watchlist.add("enwiki", "Foo")
    watchlist.apply_move("enwiki", "Foo", "Bar")
    result = await watchlist.add("enwiki", "Foo")
    assert not result.already_watched
    assert sorted(p.title for p in watchlist.pages()) == ["Bar", "Foo"]
    assert len({p.page_id for p in watchlist.pages()}) == 2
    store.close()
