"""Pilot tests for the Reading tab, fed by a fake data source (no network)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
import respx
from support import FakeBrowser, ListSource, action_api, finish_work, footer_keys, make_app
from textual.widgets import DataTable, Static

from wikihose.clock import VirtualClock
from wikihose.net import Client, FetchError
from wikihose.reading import Progress, SpikeRow, Summary
from wikihose.ui.app import WikihoseApp
from wikihose.ui.tabs.reading import (
    SPIKE_WIDTH,
    USUAL_WIDTH,
    VIEWS_WIDTH,
    ReadingTab,
    columns,
    describe,
    spike_text,
    usual_text,
)
from wikihose.ui.widgets import spark

SERIES = tuple([100] * 29 + [9000])


def make_rows(count: int = 60) -> list[SpikeRow]:
    return [
        SpikeRow(f"Page_{n}", 90000 - n * 100, 100.0, SERIES if n % 2 == 0 else (), n % 2 == 0) for n in range(count)
    ]


class FakeReading:
    def __init__(self, rows: list[SpikeRow] | None = None, error: FetchError | None = None) -> None:
        self.rows = make_rows() if rows is None else rows
        self.error = error
        self.day: date | None = None
        self.loads = 0
        self.summaries: list[str] = []

    async def load(self, progress: Progress | None = None) -> list[SpikeRow]:
        self.loads += 1
        if progress:
            progress(1, 2)
        if self.error:
            raise self.error
        self.day = date(2026, 11, 6)
        return self.rows

    async def summary(self, title: str) -> Summary | None:
        self.summaries.append(title)
        if title == "Page_3":
            return None
        return Summary(title.replace("_", " "), "a test page", f"About {title}.")


def reading_app(clock: VirtualClock, fake: FakeReading | None, **kwargs: Any) -> WikihoseApp:
    factory = (lambda _wiki: fake) if fake is not None else None
    return make_app(ListSource(clock), clock, reading=factory, **kwargs)


async def settle_reading(app: WikihoseApp, pilot: Any) -> ReadingTab:
    tab = app.query_one(ReadingTab)
    for _ in range(200):
        await pilot.pause()
        if tab._started and not tab.workers._workers:  # the load worker has finished
            break
    await pilot.pause()
    return tab


def table(app: WikihoseApp) -> DataTable[Any]:
    return app.query_one("#reading-table", DataTable)


def status(app: WikihoseApp) -> str:
    return str(app.query_one("#reading-status", Static).render())


def detail(app: WikihoseApp) -> str:
    return str(app.query_one("#reading-detail", Static).render())


# --- pure parts ------------------------------------------------------------------------------------------------------


def test_spark_scales_to_the_largest_and_merges_days() -> None:
    assert spark((), 15) == ""
    assert spark((0, 0, 8), 3)[-1] == "█" and spark((0, 0, 8), 3)[0] == "▁"
    assert len(spark(SERIES, 15)) == 15 and len(spark(SERIES, 30)) == 30
    assert spark(tuple(range(30)), 15)[-1] == "█"


def test_rough_figures_carry_a_tilde() -> None:
    exact = SpikeRow("A", 50000, 100.0, SERIES, True)
    rough = SpikeRow("A", 50000, 100.0, (), False)
    assert (spike_text(exact), usual_text(exact)) == ("500x", "100")
    assert (spike_text(rough), usual_text(rough)) == ("~500x", "~100")


def test_a_page_with_too_little_history_reads_new_not_a_huge_multiple() -> None:
    row = SpikeRow("Vali_Chandrasekaran", 52699, 0.0, SERIES, True)
    assert row.new and row.multiple == 0
    assert (spike_text(row), usual_text(row)) == ("new", "0")
    assert not SpikeRow("A", 52699, 100.0, SERIES, True).new  # a usual of exactly MIN_USUAL has a multiple


@pytest.mark.parametrize("width", [80, 100, 120, 200])
def test_columns_fit_without_sideways_scrolling(width: int) -> None:
    article, cells = columns(width)
    assert article >= 10
    assert article + VIEWS_WIDTH + USUAL_WIDTH + SPIKE_WIDTH + cells + 2 * 5 + 2 <= width


def test_describe_names_the_problem() -> None:
    assert "rate limited, retrying in 12s" in describe(FetchError("rate_limited", "x", retry_after=12))
    assert "could not reach" in describe(FetchError("down", "x"))
    assert "unexpected" in describe(FetchError("bad_data", "x"))


# --- the tab ---------------------------------------------------------------------------------------------------------


async def test_nothing_is_fetched_until_the_tab_is_opened(clock: VirtualClock) -> None:
    fake = FakeReading()
    app = reading_app(clock, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert fake.loads == 0
        await pilot.press("2")
        await settle_reading(app, pilot)
        assert fake.loads == 1


async def test_shows_25_rows_by_default_with_the_day_and_the_columns(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        assert table(app).row_count == 25
        assert [str(c.label) for c in table(app).columns.values()] == ["Article", "Views", "Usual", "Spike", "30 days"]
        assert "6 Nov (UTC)" in status(app) and "25 of 60" in status(app)
        assert app.focused is table(app)


async def test_n_cycles_through_25_50_and_100_rows(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading(make_rows(120)))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        counts = []
        for _ in range(3):
            await pilot.press("n")
            await pilot.pause()
            counts.append(table(app).row_count)
        assert counts == [50, 100, 25]


async def test_r_fetches_again(clock: VirtualClock) -> None:
    fake = FakeReading()
    app = reading_app(clock, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("r")
        await settle_reading(app, pilot)
        assert fake.loads == 2


async def test_the_detail_pane_follows_the_cursor(clock: VirtualClock) -> None:
    fake = FakeReading()
    app = reading_app(clock, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        for _ in range(50):
            await pilot.pause()
            if "About Page_0." in detail(app):
                break
        assert "Page 0" in detail(app) and "a test page" in detail(app) and "About Page_0." in detail(app)
        assert "90,000 views" in detail(app) and "900x" in detail(app)
        await pilot.press("down", "down", "down")  # Page_3 has no summary
        for _ in range(50):
            await pilot.pause()
            if "No summary available." in detail(app):
                break
        assert "No summary available." in detail(app)


async def test_rough_rows_show_a_tilde_in_the_table(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        cells = [str(table(app).get_cell_at((row, 3))) for row in (0, 1)]
        assert cells[0] == "900x" and cells[1].startswith("~")


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (FetchError("rate_limited", "HTTP 429", retry_after=12), "rate limited, retrying in 12s"),
        (FetchError("down", "HTTP 500"), "could not reach Wikimedia"),
    ],
)
async def test_a_failed_load_says_why(clock: VirtualClock, error: FetchError, words: str) -> None:
    app = reading_app(clock, FakeReading(error=error))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        for _ in range(50):
            await pilot.pause()
        assert words in status(app)
        assert table(app).row_count == 0


async def test_an_empty_day_says_so(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading(rows=[]))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        for _ in range(50):
            await pilot.pause()
        assert "No spikes found" in status(app)


async def test_a_replay_has_no_reading_data(clock: VirtualClock) -> None:
    app = reading_app(clock, None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert "needs a live connection" in status(app)


@respx.mock
async def test_w_on_a_reading_row_watches_that_page(clock: VirtualClock, client: Client) -> None:
    action_api((7, "Page 0", 70))
    app = reading_app(clock, FakeReading(), client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("w")
        await finish_work(app, pilot)
        assert ("enwiki", "Page 0") in app.watchlist


async def test_changing_the_wiki_in_live_reloads_reading(clock: VirtualClock) -> None:
    wikis: list[str] = []
    fake = FakeReading()

    def factory(wiki: str) -> FakeReading:
        wikis.append(wiki)
        return fake

    app = make_app(ListSource(clock), clock, reading=factory)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        app.query_one(ReadingTab).set_wiki("dewiki")
        await settle_reading(app, pilot)
        assert wikis == ["enwiki", "dewiki"] and fake.loads == 2


async def test_footer_keys_refresh_and_rows_work_when_clicked(clock: VirtualClock) -> None:
    fake = FakeReading()
    app = reading_app(clock, fake)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        keys = await footer_keys(pilot, app, needing="Refresh")
        await pilot.click(next(k for k in keys if "Refresh" in str(k.render())))
        await settle_reading(app, pilot)  # the reload runs in a worker
        assert fake.loads == 2
        keys = await footer_keys(pilot, app, needing="Rows")
        await pilot.click(next(k for k in keys if "Rows" in str(k.render())))
        await pilot.pause()
        assert table(app).row_count == 50


async def test_help_on_the_reading_tab_lists_its_keys(clock: VirtualClock) -> None:
    from test_app_shell import help_keys

    app = reading_app(clock, FakeReading())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.press("question_mark")
        await pilot.pause()
        assert help_keys(app) == ["enter", "r", "n", "w", "W", "?", "q", "1-5", "Double", "Tab", "Mouse"]


async def test_n_and_r_in_a_replay_keep_the_replay_message(clock: VirtualClock) -> None:
    app = reading_app(clock, None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.press("n", "r")
        await pilot.pause()
        assert "needs a live connection" in status(app)


async def test_n_keeps_an_error_message_showing(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading(error=FetchError("down", "HTTP 500")))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("n")
        await pilot.pause()
        assert "could not reach" in status(app)


async def test_the_cursor_stays_on_its_article_through_n_and_r(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading(make_rows(120)))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("down", "down", "down", "down")
        await pilot.press("n")
        await pilot.pause()
        assert table(app).cursor_key() == "Page_4"
        await pilot.press("r")
        await settle_reading(app, pilot)
        assert table(app).cursor_key() == "Page_4"


async def test_a_failed_summary_is_tried_again_when_the_row_is_visited_again(clock: VirtualClock) -> None:
    fake = FakeReading()
    app = reading_app(clock, fake)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("down", "down", "down")  # Page_3 has no summary
        for _ in range(20):
            await pilot.pause()
        await pilot.press("up")
        await pilot.press("down")
        for _ in range(20):
            await pilot.pause()
        assert fake.summaries.count("Page_3") >= 2


async def test_a_rate_limit_retry_is_cancelled_by_a_wiki_change(clock: VirtualClock) -> None:
    limited = FakeReading(error=FetchError("rate_limited", "HTTP 429", retry_after=30))
    good = FakeReading()

    def factory(wiki: str) -> FakeReading:
        return limited if wiki == "enwiki" else good

    app = make_app(ListSource(clock), clock, reading=factory)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        tab = app.query_one(ReadingTab)
        assert "rate limited" in status(app) and tab._retry is not None
        tab.set_wiki("dewiki")
        await settle_reading(app, pilot)
        assert tab._retry is None and good.loads == 1 and limited.loads == 1
        assert table(app).row_count == 25


async def test_a_load_that_finishes_after_a_wiki_change_is_dropped(clock: VirtualClock) -> None:
    class Slow(FakeReading):
        async def load(self, progress: Any = None) -> list[SpikeRow]:
            await asyncio.sleep(0.2)
            return await super().load(progress)

    slow, good = Slow(), FakeReading(make_rows(30))
    app = make_app(ListSource(clock), clock, reading=lambda wiki: slow if wiki == "enwiki" else good)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        app.query_one(ReadingTab).set_wiki("dewiki")
        await settle_reading(app, pilot)
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert table(app).row_count == 25 and "25 of 30" in status(app)


async def test_a_long_summary_is_cut_with_an_ellipsis_inside_the_pane(clock: VirtualClock) -> None:
    class Wordy(FakeReading):
        async def summary(self, title: str) -> Summary | None:
            return Summary(title, "a test page", "word " * 200)

    app = reading_app(clock, Wordy())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        for _ in range(30):
            await pilot.pause()
        text = detail(app)
        assert text.rstrip().endswith("…") and len(text) < 330
        assert text.count(chr(10)) < 7


async def test_wide_numbers_fit_their_columns(clock: VirtualClock) -> None:
    big = SpikeRow("Big", 10_000_000, 100.0, (), False)
    app = reading_app(clock, FakeReading([big]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        assert (
            str(table(app).get_cell_at((0, 1))) == "10,000,000"
            and len(str(table(app).get_cell_at((0, 1)))) <= VIEWS_WIDTH
        )
        assert len(str(table(app).get_cell_at((0, 3)))) <= SPIKE_WIDTH


async def test_enter_and_double_click_on_a_reading_row_open_that_page_in_the_browser(
    clock: VirtualClock, browser: FakeBrowser
) -> None:
    from test_app_shell import click_footer

    app = reading_app(clock, FakeReading())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        assert app.focused is table(app)
        await pilot.press("enter")
        await pilot.pause()
        assert browser.opened == ["https://en.wikipedia.org/wiki/Page_0"]
        await pilot.double_click("#reading-table", offset=(10, 3))  # the header is row 0, so this is the third page
        await pilot.pause()
        assert browser.opened[1:] == ["https://en.wikipedia.org/wiki/Page_2"]
        await click_footer(pilot, app, "Open")  # the footer's enter Open does what the key does
        assert browser.opened[2:] == ["https://en.wikipedia.org/wiki/Page_2"]
        await pilot.click("#reading-table", offset=(10, 4))  # a single click only moves the cursor
        await pilot.pause()
        assert len(browser.opened) == 3


async def test_enter_on_an_empty_reading_table_does_nothing(clock: VirtualClock, browser: FakeBrowser) -> None:
    app = reading_app(clock, FakeReading([]))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert browser.opened == []
