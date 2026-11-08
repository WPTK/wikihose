"""Pilot tests for the Reading tab, fed by a fake data source (no network)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from support import ListSource, footer_keys, make_app
from textual.widgets import DataTable, Static

from wikihose.clock import VirtualClock
from wikihose.net import FetchError
from wikihose.reading import Progress, SpikeRow, Summary
from wikihose.ui.app import WikihoseApp
from wikihose.ui.tabs.reading import ReadingTab, columns, describe, spark, spike_text, usual_text

SERIES = tuple([100] * 29 + [9000])


def make_rows(count: int = 60) -> list[SpikeRow]:
    return [
        SpikeRow(f"Page_{n}", 90000 - n * 100, 100.0, (90000 - n * 100) / 100, SERIES if n % 2 == 0 else (), n % 2 == 0)
        for n in range(count)
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
        if tab.query_one("#reading-table", DataTable).row_count or "Loading" not in tab._message:
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
    exact = SpikeRow("A", 5000, 10.0, 500.0, SERIES, True)
    rough = SpikeRow("A", 5000, 10.0, 500.0, (), False)
    assert (spike_text(exact), usual_text(exact)) == ("500x", "10")
    assert (spike_text(rough), usual_text(rough)) == ("~500x", "~10")


@pytest.mark.parametrize("width", [80, 100, 120, 200])
def test_columns_fit_without_sideways_scrolling(width: int) -> None:
    article, cells = columns(width)
    assert article + 9 + 9 + 8 + cells + 2 * 5 + 2 <= width or article == 10


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


async def test_w_on_a_reading_row_watches_that_page(clock: VirtualClock) -> None:
    app = reading_app(clock, FakeReading())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        await pilot.press("w")
        await pilot.pause()
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
        for label in ("Refresh", "Rows"):
            keys = await footer_keys(pilot, app, needing=label)
            await pilot.click(next(k for k in keys if label in str(k.render())))
            await pilot.pause()
        assert fake.loads == 2 and table(app).row_count == 50


async def test_help_on_the_reading_tab_lists_its_keys(clock: VirtualClock) -> None:
    from test_app_shell import help_keys

    app = reading_app(clock, FakeReading())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.press("question_mark")
        await pilot.pause()
        assert help_keys(app) == ["r", "n", "w", "?", "q", "1-5", "Enter", "Tab", "Mouse"]
