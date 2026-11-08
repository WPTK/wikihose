"""The Convergence tab: the numbers (no Textual), the surge fetch through the Reading data, and Pilot tests."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx
import pytest
import respx
from support import FakeBrowser, ListSource, make_app, make_edit, wait_for_stream
from test_reading import NEWEST, history_payload, mock_wiki
from textual.widgets import Static

from wikihose.clock import VirtualClock
from wikihose.convergence import LABEL, MIN_SPIKE, MIN_SURGE_VIEWS, SURGE_EDITS, WINDOW, Convergence
from wikihose.net import Client, FetchError
from wikihose.reading import DAYS, MIN_USUAL, Progress, ReadingData, SpikeRow, Summary
from wikihose.sources.events import StreamItem
from wikihose.ui.app import WikihoseApp
from wikihose.ui.tabs.convergence import DOWN_PAUSE, ConvergenceTab
from wikihose.ui.widgets import PageTable

DAY = date(2026, 11, 6)


def spike(title: str, multiple: float, views: int = 50000) -> SpikeRow:
    return SpikeRow(title, views, views / multiple, (), True)


def edits(title: str, count: int, **kwargs: Any) -> list[StreamItem]:
    return [make_edit(title, **kwargs) for _ in range(count)]


# --- the numbers -----------------------------------------------------------------------------------------------------


def test_only_edits_by_people_to_articles_of_the_wiki_count(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    for item in [
        make_edit("Ada Lovelace"),
        make_edit("Ada_Lovelace"),  # the same page, written the API's way
        make_edit("Ada Lovelace", bot=True),
        make_edit("Ada Lovelace", namespace=1),
        make_edit("Ada Lovelace", wiki="dewiki"),
    ]:
        model.add(item)  # type: ignore[arg-type]
    assert model.edits("Ada Lovelace") == 2


def test_edits_leave_the_window(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    model.add(make_edit("Ada Lovelace"))
    clock.advance(WINDOW / 2)
    model.add(make_edit("Ada Lovelace"))
    clock.advance(WINDOW / 2)
    assert model.edits("Ada Lovelace") == 1
    clock.advance(WINDOW)
    assert model.edits("Ada Lovelace") == 0
    assert model.ranked() == []


def test_ranked_by_edits_times_spike_and_only_when_hot_in_both(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    model.set_reading(
        [spike("Big_Spike", 40.0), spike("Busy_Page", 5.0), spike("Not_Spiking", MIN_SPIKE - 0.5), spike("Unedited", 9)]
    )
    for title, count in [("Big Spike", 1), ("Busy Page", 10), ("Not Spiking", 50), ("Only Live", 3)]:
        for _ in range(count):
            model.add(make_edit(title))
    ranked = model.ranked()
    assert [(r.title, r.edits, r.score) for r in ranked] == [("Busy Page", 10, 50.0), ("Big Spike", 1, 40.0)]
    assert not any(r.surge for r in ranked)


def test_the_surge_trigger_asks_once_per_title_and_never_for_a_reading_title(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    model.set_reading([spike("Known", 3.0)])
    for _ in range(SURGE_EDITS - 1):
        model.add(make_edit("Hot"))
        model.add(make_edit("Known"))
    assert model.next_surge() is None  # one short of the threshold
    model.add(make_edit("Hot"))
    model.add(make_edit("Known"))
    assert model.next_surge() == "Hot"
    for _ in range(SURGE_EDITS * 2):
        model.add(make_edit("Hot"))
    assert model.next_surge() is None  # already asked
    model.requeue("Hot")
    assert model.next_surge() == "Hot"  # a rate-limited fetch goes back to the front


def test_a_queued_surge_is_dropped_when_the_reading_ranking_arrives_with_it(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    for _ in range(SURGE_EDITS):
        model.add(make_edit("Late Arrival"))
    model.set_reading([spike("Late_Arrival", 4.0)])
    assert model.next_surge() is None
    assert model.ranked()[0].surge is False


def test_a_title_that_stopped_surging_while_queued_is_dropped_and_can_surge_again(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    for _ in range(SURGE_EDITS):
        model.add(make_edit("Old News"))
    assert model.pending
    clock.advance(WINDOW + 1)  # hours on the Live tab: the surge is over before anyone asks
    assert model.next_surge() is None and not model.pending
    for _ in range(SURGE_EDITS):
        model.add(make_edit("Old News"))
    assert model.next_surge() == "Old News"  # a new surge is fetched after all


def test_a_surge_row_needs_enough_views(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    for title in ("Tiny", "Big"):
        for _ in range(SURGE_EDITS):
            model.add(make_edit(title))
    model.add_surge("Tiny", spike("Tiny", 2.0, views=MIN_SURGE_VIEWS - 1))  # a usual of 499.5: not new
    model.add_surge("Big", spike("Big", 3.0, views=MIN_SURGE_VIEWS))
    assert [r.title for r in model.ranked()] == ["Big"]


def test_a_new_page_is_not_hot_in_reading(clock: VirtualClock) -> None:
    # #26: the rule Reading ranks by. A page read less than MIN_USUAL a day has no multiple, from either source
    model = Convergence("enwiki", clock)
    model.set_reading([SpikeRow("New_In_Reading", 52699, 0.0, (), True), spike("Old", 3.0)])
    for title in ("New In Reading", "New Surge", "Old"):
        for _ in range(SURGE_EDITS):
            model.add(make_edit(title))
    model.add_surge("New Surge", SpikeRow("New_Surge", 50000, MIN_USUAL - 1, (), True))
    assert [r.title for r in model.ranked()] == ["Old"]


def test_a_surge_row_ranks_with_its_own_numbers(clock: VirtualClock) -> None:
    model = Convergence("enwiki", clock)
    for _ in range(SURGE_EDITS):
        model.add(make_edit("Hot"))
    assert model.next_surge() == "Hot"
    model.add_surge("Hot", spike("Hot", 3.0))
    model.add_surge("Nothing", None)
    (row,) = model.ranked()
    assert row.surge and row.score == SURGE_EDITS * 3.0
    assert model.surges == 2


# --- the surge fetch -------------------------------------------------------------------------------------------------


@respx.mock
async def test_spike_of_fetches_one_history_and_caches_it(client: Client, clock: VirtualClock) -> None:
    clock.advance((NEWEST - clock.now().date()).days * 86400 + 86400)  # the day after NEWEST
    mock_wiki(respx.mock)
    first = date.fromordinal(NEWEST.toordinal() - (DAYS - 1))
    route = respx.get(url__regex=r".*/per-article/.*/Rising_Star/daily/.*").mock(
        return_value=httpx.Response(200, json=history_payload(first, dict.fromkeys(range(DAYS - 1), 100) | {29: 2500}))
    )
    data = ReadingData(client, clock)
    assert await data.spike_of("Rising Star") is None  # no ranked day yet
    data.day = (await data.newest_day() or (None,))[0]  # what load() ranks; the rest of load is tested elsewhere
    row = await data.spike_of("Rising Star")
    assert row is not None and row.exact
    assert (row.title, row.views, row.baseline, row.multiple) == ("Rising_Star", 2500, 100.0, 25.0)
    assert await data.spike_of("Rising Star") == row
    assert route.call_count == 1  # fetched once, then cached


@respx.mock
async def test_spike_of_uses_the_top_list_figure_and_handles_no_data(client: Client, clock: VirtualClock) -> None:
    clock.advance((NEWEST - clock.now().date()).days * 86400 + 86400)
    today = mock_wiki(respx.mock)
    first = date.fromordinal(NEWEST.toordinal() - (DAYS - 1))
    respx.get(url__regex=r".*/per-article/.*/Spiky/daily/.*").mock(
        return_value=httpx.Response(200, json=history_payload(first, dict.fromkeys(range(DAYS), 10)))
    )
    respx.get(url__regex=r".*/per-article/.*/Gone/daily/.*").mock(return_value=httpx.Response(404))
    data = ReadingData(client, clock)
    data.day = (await data.newest_day() or (None,))[0]  # what load() ranks; the rest of load is tested elsewhere
    row = await data.spike_of("Spiky")
    assert row is not None and row.views == today["Spiky"]
    assert await data.spike_of("Gone") is None
    assert await data.spike_of("\ud800") is None  # cannot go into a URL


# --- the tab ---------------------------------------------------------------------------------------------------------


class FakeReading:
    """Reading data with a fixed ranking and a surge answer per title (no network)."""

    def __init__(
        self,
        rows: list[SpikeRow],
        surges: dict[str, SpikeRow | FetchError | None] | None = None,
        load_error: FetchError | None = None,
    ) -> None:
        self.rows = rows
        self.surges = surges or {}
        self.load_error = load_error
        self.day: date | None = None
        self.asked: list[str] = []
        self.hold: asyncio.Event | None = None  # when set, spike_of waits for it

    async def load(self, progress: Progress | None = None) -> list[SpikeRow]:
        if self.load_error is not None:
            raise self.load_error
        self.day = DAY
        return self.rows

    async def summary(self, title: str) -> Summary | None:
        return None

    async def spike_of(self, title: str) -> SpikeRow | None:
        self.asked.append(title)
        if self.hold is not None:
            await self.hold.wait()
        answer = self.surges.get(title, self.surges.get(title.replace(" ", "_")))
        if isinstance(answer, FetchError):
            raise answer
        return answer


def convergence_app(clock: VirtualClock, fake: FakeReading | None, items: list[StreamItem]) -> WikihoseApp:
    factory = (lambda _wiki: fake) if fake is not None else None
    return make_app(ListSource(clock, items), clock, reading=factory)


async def open_tab(app: WikihoseApp, pilot: Any) -> ConvergenceTab:
    await wait_for_stream(app, pilot)
    await pilot.press("5")
    tab = app.query_one(ConvergenceTab)
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()
        tab.tick()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return tab


async def open_tab_holding(app: WikihoseApp, pilot: Any, fake: FakeReading) -> ConvergenceTab:
    """Open the tab and wait until the surge worker is stuck in ``fake.spike_of``."""
    await wait_for_stream(app, pilot)
    await pilot.press("5")
    tab = app.query_one(ConvergenceTab)
    for _ in range(20):
        await pilot.pause()
        tab.tick()
        if fake.asked:
            break
    assert fake.asked, "the surge fetch never started"
    return tab


def text_of(app: WikihoseApp, widget_id: str) -> str:
    return str(app.query_one(f"#{widget_id}", Static).render())


def table_rows(app: WikihoseApp) -> list[list[str]]:
    table = app.query_one("#convergence-table", PageTable)
    return [[str(cell) for cell in table.get_row_at(n)] for n in range(table.row_count)]


async def test_opening_the_tab_loads_reading_and_ranks_the_overlap(clock: VirtualClock) -> None:
    fake = FakeReading([spike("Busy_Page", 5.0), spike("Big_Spike", 40.0), spike("Quiet", 30.0)])
    items = edits("Busy Page", 10) + edits("Big Spike", 1) + edits("Bots Only", 20, bot=True)
    app = convergence_app(clock, fake, items)
    async with app.run_test(size=(80, 24)) as pilot:
        await open_tab(app, pilot)
        assert [row[0] for row in table_rows(app)] == ["Busy Page", "Big Spike"]
        assert table_rows(app)[0][1:4] == ["10", "5x", "50"]
        heading = text_of(app, "convergence-heading")
        assert heading.lower().startswith(LABEL) and "6 Nov (UTC)" in heading
        assert "2 hot in both" in text_of(app, "convergence-status")
        assert "Busy Page" in text_of(app, "convergence-detail")
        assert fake.asked == []  # nothing reached the surge threshold outside the ranking


async def test_a_surge_fetches_history_once_and_joins_the_list(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Hot Topic": spike("Hot_Topic", 12.0), "Dull": spike("Dull", 1.0)})
    items = edits("Hot Topic", SURGE_EDITS * 2) + edits("Dull", SURGE_EDITS) + edits("Few", SURGE_EDITS - 1)
    app = convergence_app(clock, fake, items)
    async with app.run_test(size=(100, 30)) as pilot:
        await open_tab(app, pilot)
        assert fake.asked == ["Hot Topic", "Dull"]  # once each, never for a page below the threshold
        rows = table_rows(app)
        assert [row[0] for row in rows] == ["Hot Topic"]  # Dull is not spiking in Reading
        assert rows[0][4] == "surge"
        assert "2 surge checks" in text_of(app, "convergence-status")


async def test_a_rate_limited_surge_waits_and_is_tried_again(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Hot": FetchError("rate_limited", "slow down", retry_after=30)})
    app = convergence_app(clock, fake, edits("Hot", SURGE_EDITS))
    async with app.run_test(size=(80, 24)) as pilot:
        tab = await open_tab(app, pilot)
        assert fake.asked == ["Hot"]
        assert "rate limited" in text_of(app, "convergence-status")
        fake.surges["Hot"] = spike("Hot", 4.0)
        clock.advance(31)
        tab.tick()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.asked == ["Hot", "Hot"]
        assert [row[0] for row in table_rows(app)] == ["Hot"]


async def test_a_surge_that_cannot_reach_wikimedia_waits_a_minute(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Hot": FetchError("down", "no route")})
    app = convergence_app(clock, fake, edits("Hot", SURGE_EDITS))
    async with app.run_test(size=(80, 24)) as pilot:
        tab = await open_tab(app, pilot)
        assert fake.asked == ["Hot"]
        assert f"again in {int(DOWN_PAUSE)}s" in text_of(app, "convergence-status")
        clock.advance(DOWN_PAUSE - 1)
        tab.tick()
        await app.workers.wait_for_complete()
        assert fake.asked == ["Hot"]  # still waiting
        fake.surges["Hot"] = spike("Hot", 4.0)
        clock.advance(2)
        tab.tick()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.asked == ["Hot", "Hot"]
        assert "rate" not in text_of(app, "convergence-status") and "1 hot in both" in text_of(
            app, "convergence-status"
        )


async def test_bad_data_for_one_surge_moves_on_to_the_next(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Broken": FetchError("bad_data", "junk"), "Fine": spike("Fine", 5.0)})
    app = convergence_app(clock, fake, edits("Broken", SURGE_EDITS) + edits("Fine", SURGE_EDITS))
    async with app.run_test(size=(80, 24)) as pilot:
        await open_tab(app, pilot)
        assert fake.asked == ["Broken", "Fine"]
        assert [row[0] for row in table_rows(app)] == ["Fine"]
        assert "2 surge checks" in text_of(app, "convergence-status")


async def test_a_wiki_change_during_a_surge_fetch_drops_its_answer(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Hot": FetchError("rate_limited", "slow down", retry_after=30)})
    fake.hold = asyncio.Event()
    app = convergence_app(clock, fake, edits("Hot", SURGE_EDITS))
    async with app.run_test(size=(80, 24)) as pilot:
        tab = await open_tab_holding(app, pilot, fake)
        app.screen.set_reading_wiki("dewiki")  # type: ignore[attr-defined]
        fake.hold.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert tab.wiki == "dewiki" and tab.model.wiki == "dewiki"
        assert "rate limited" not in text_of(app, "convergence-status")  # the old fetch's error is not shown
        assert not tab.model.pending


async def test_quitting_during_a_surge_fetch_exits_without_an_error(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Hot": spike("Hot", 4.0)})
    fake.hold = asyncio.Event()
    app = convergence_app(clock, fake, edits("Hot", SURGE_EDITS))
    async with app.run_test(size=(80, 24)) as pilot:
        await open_tab_holding(app, pilot, fake)
        await pilot.press("q")
        await pilot.pause()
    assert app.return_code in (0, None)


async def test_the_trigger_fetches_nothing_until_the_tab_is_opened(clock: VirtualClock) -> None:
    fake = FakeReading([], {"Hot": spike("Hot", 4.0)})
    app = convergence_app(clock, fake, edits("Hot", SURGE_EDITS))
    async with app.run_test(size=(80, 24)) as pilot:
        await wait_for_stream(app, pilot)
        await pilot.press("2")  # Reading loads, so only the unopened tab holds the trigger back
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.day == DAY
        app.query_one(ConvergenceTab).tick()
        await app.workers.wait_for_complete()
        assert fake.asked == []


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (None, "needs the Reading numbers"),
        (FakeReading([]), "Reading found no spikes"),
        (FakeReading([], load_error=FetchError("down", "no route")), "could not reach Wikimedia"),
        (FakeReading([spike("Unedited", 9.0)]), "Nothing is hot in both"),
    ],
)
async def test_the_empty_tab_says_why(clock: VirtualClock, fake: FakeReading | None, expected: str) -> None:
    app = convergence_app(clock, fake, [])
    async with app.run_test(size=(80, 24)) as pilot:
        await open_tab(app, pilot)
        text = text_of(app, "convergence-empty")
        assert expected in text and f"{SURGE_EDITS} edits" in text
        assert not app.query_one("#convergence-table", PageTable).display


async def test_the_table_takes_the_focus_and_w_names_the_page_at_the_cursor(clock: VirtualClock) -> None:
    fake = FakeReading([spike("Busy_Page", 5.0)])
    app = convergence_app(clock, fake, edits("Busy Page", 3))
    async with app.run_test(size=(80, 24)) as pilot:
        await open_tab(app, pilot)
        table = app.query_one("#convergence-table", PageTable)
        assert app.focused is table
        assert table.page_at_cursor() == ("enwiki", "Busy Page")


async def test_enter_and_double_click_on_a_convergence_row_open_that_page_in_the_browser(
    clock: VirtualClock, browser: FakeBrowser
) -> None:
    from test_app_shell import click_footer

    fake = FakeReading([spike("Busy_Page", 5.0), spike("Big_Spike", 40.0)])
    app = convergence_app(clock, fake, edits("Busy Page", 10) + edits("Big Spike", 2))
    async with app.run_test(size=(100, 30)) as pilot:
        await open_tab(app, pilot)
        assert app.focused is app.query_one("#convergence-table", PageTable)
        await pilot.press("enter")
        await pilot.pause()
        assert browser.opened == ["https://en.wikipedia.org/wiki/Big_Spike"]  # 2 edits x 40 outranks 10 x 5
        await pilot.double_click("#convergence-table", offset=(10, 2))  # the header is row 0
        await pilot.pause()
        assert browser.opened[1:] == ["https://en.wikipedia.org/wiki/Busy_Page"]
        await click_footer(pilot, app, "Open")  # the footer's enter Open does what the key does
        assert browser.opened[2:] == ["https://en.wikipedia.org/wiki/Busy_Page"]
        await pilot.press("question_mark")
        await pilot.pause()
        keys = [str(line.render()).split()[0] for line in app.screen.query(".help-line")]
        assert keys == ["enter", "w", "W", "?", "q", "1-5", "Double", "Tab", "Mouse"]


async def test_a_new_wiki_starts_the_counts_over(clock: VirtualClock) -> None:
    fake = FakeReading([spike("Busy_Page", 5.0)])
    app = convergence_app(clock, fake, edits("Busy Page", 3))
    async with app.run_test(size=(80, 24)) as pilot:
        tab = await open_tab(app, pilot)
        tab.set_wiki("dewiki")
        assert tab.model.ranked() == [] and tab.model.wiki == "dewiki"
        tab.set_wiki("dewiki")  # the same wiki again changes nothing


async def test_a_looping_demo_keeps_the_reading_ranking(clock: VirtualClock) -> None:
    fake = FakeReading([spike("Busy_Page", 5.0)])
    app = convergence_app(clock, fake, edits("Busy Page", 3))
    async with app.run_test(size=(80, 24)) as pilot:
        tab = await open_tab(app, pilot)
        tab.start_over()
        tab.accept(make_edit("Busy Page"))
        assert [r.title for r in tab.model.ranked()] == ["Busy Page"]


@pytest.mark.parametrize(("width", "height"), [(80, 24), (120, 30), (160, 48), (240, 60)])
async def test_the_columns_fit_without_scrolling_sideways(clock: VirtualClock, width: int, height: int) -> None:
    fake = FakeReading([spike("A_very_long_article_title_that_goes_on_and_on_" * 4, 5.0)])
    app = convergence_app(clock, fake, edits("A very long article title that goes on and on " * 4, 3))
    async with app.run_test(size=(width, height)) as pilot:
        await open_tab(app, pilot)
        table = app.query_one("#convergence-table", PageTable)
        assert table.row_count == 1
        assert table.virtual_size.width <= table.scrollable_content_region.width
        assert table.max_scroll_x == 0


async def test_a_failed_reload_keeps_the_earlier_ranking(clock: VirtualClock) -> None:
    fake = FakeReading([spike("Unedited", 9.0)])
    app = convergence_app(clock, fake, [])
    async with app.run_test(size=(80, 24)) as pilot:
        tab = await open_tab(app, pilot)
        tab.set_reading("enwiki", [], "Pageviews: could not reach Wikimedia. Press r to try again.")
        await pilot.pause()
        assert "Nothing is hot in both" in text_of(app, "convergence-empty")
