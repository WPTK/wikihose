"""Pilot tests for the Live tab."""

from __future__ import annotations

import pytest
from support import EASTERN, ListSource, live_tab, make_app, make_edit, settle
from textual.widgets import DataTable, Input, Sparkline, Static

from wikihose.clock import VirtualClock
from wikihose.config import Config
from wikihose.sources.events import Edit
from wikihose.store import Store
from wikihose.ui.dialogs import EditDetail, WikiPrompt
from wikihose.ui.tabs.live import format_delta, log_columns, size_words

MIXED = [
    make_edit("Human article", delta=500, user="Alice"),
    make_edit("Bot article", delta=300, bot=True, user="BotBot"),
    make_edit("Talk:Something", namespace=1, delta=200, user="Carol"),
    make_edit("Tiny change", delta=5, user="Dave"),
    make_edit("Big change", delta=2500, user="Erin"),
]


def app_with(clock: VirtualClock, edits: list[Edit], **kwargs):
    return make_app(ListSource(clock, list(edits)), clock, **kwargs)


def filters_text(app) -> str:
    return str(app.query_one("#filters").render())


def log_titles(app) -> list[str]:
    table = app.query_one("#log", DataTable)
    return [
        str(table.get_cell(row.key, "Article")).removeprefix("bot ").removeprefix("new ") for row in table.ordered_rows
    ]


# --- pure helpers ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "text"),
    [(0, "+0"), (5, "+5"), (-5, "-5"), (1234, "+1,234"), (-99999, "-99,999"), (123456, "+123k"), (-2097152, "-2097k")],
)
def test_format_delta_fits_seven_cells(delta: int, text: str) -> None:
    assert format_delta(delta) == text
    assert len(text) <= 7


@pytest.mark.parametrize(("n", "words"), [(0, "any size"), (100, "100+ bytes"), (1000, "1,000+ bytes")])
def test_size_words(n: int, words: str) -> None:
    assert size_words(n) == words


@pytest.mark.parametrize("width", [80, 104, 105, 120, 144, 145, 160, 240])
def test_columns_always_fit_the_log(width: int) -> None:
    columns = log_columns(width)
    labels = [label for label, _ in columns]
    assert labels[:3] == ["Time", "Bytes", "Article"]
    assert ("Editor" in labels) == (width >= 105)
    assert ("Summary" in labels) == (width >= 145)
    used = sum(w + 2 for _, w in columns)
    assert used <= width - 34 - 2  # the side column and the log's scrollbar
    assert dict(columns)["Article"] >= 8


# --- the log ---------------------------------------------------------------------------------------------------------


async def test_log_defaults_to_articles_by_people_newest_first(clock: VirtualClock) -> None:
    app = app_with(clock, MIXED)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert log_titles(app) == ["Big change", "Tiny change", "Human article"]
        assert filters_text(app) == "enwiki · bots hidden · articles only · any size · live"


async def test_log_cells_show_local_time_coloured_bytes_and_new_pages(clock: VirtualClock) -> None:
    app = app_with(
        clock,
        [
            make_edit("Gain", delta=1234),
            make_edit("Loss", delta=-56),
            make_edit("Same", delta=0),
            make_edit("Fresh", kind="new", delta=300),
        ],
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#log", DataTable)
        rows = {str(table.get_cell(r.key, "Article")).removeprefix("new "): r.key for r in table.ordered_rows}
        assert str(table.get_cell(rows["Gain"], "Time")) == "14:41:05"  # 19:41:05 UTC is 14:41:05 in New York
        theme = app.current_theme
        gain, loss, same = (table.get_cell(rows[t], "Bytes") for t in ("Gain", "Loss", "Same"))
        assert (str(gain), str(loss), str(same)) == ("+1,234", "-56", "+0")
        assert theme.success in str(gain.style)
        assert theme.error in str(loss.style)
        assert "dim" in str(same.style)
        assert str(table.get_cell(rows["Fresh"], "Article")).startswith("new Fresh")
        assert "bold" in str(table.get_cell(rows["Fresh"], "Article").spans[0].style)


async def test_bots_shown_are_dim_with_a_bot_prefix(clock: VirtualClock) -> None:
    app = app_with(clock, MIXED)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("b")
        await pilot.pause()
        assert log_titles(app) == ["Big change", "Tiny change", "Bot article", "Human article"]
        table = app.query_one("#log", DataTable)
        bot_row = next(r for r in table.ordered_rows if "Bot article" in str(table.get_cell(r.key, "Article")))
        cell = table.get_cell(bot_row.key, "Article")
        assert str(cell) == "bot Bot article"
        assert "dim" in str(cell.spans[0].style)
        assert "dim" in str(cell.spans[1].style)
        assert filters_text(app).startswith("enwiki · bots shown")


async def test_a_toggle_refills_the_log_from_the_last_edits(clock: VirtualClock) -> None:
    app = app_with(clock, MIXED)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("a")
        await pilot.pause()
        assert log_titles(app) == ["Big change", "Tiny change", "Talk:Something", "Human article"]
        assert "all pages" in filters_text(app)
        await pilot.press("a")
        await pilot.pause()
        assert "Talk:Something" not in log_titles(app)


async def test_size_key_cycles_through_the_steps(clock: VirtualClock) -> None:
    app = app_with(clock, MIXED)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.pause()
        assert log_titles(app) == ["Big change", "Human article"]
        assert "100+ bytes" in filters_text(app)
        await pilot.press("m")
        await pilot.pause()
        assert log_titles(app) == ["Big change"]
        assert "1,000+ bytes" in filters_text(app)
        await pilot.press("m")
        await pilot.pause()
        assert len(log_titles(app)) == 3
        assert "any size" in filters_text(app)


async def test_size_filter_uses_the_absolute_change(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("Shrunk", delta=-900), make_edit("Grew", delta=50)], config=Config(min_bytes=100))
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert log_titles(app) == ["Shrunk"]


def click_offset(app, words: str) -> tuple[int, int]:
    line = app.query_one("#filters")
    return (line.region.x + str(line.render()).index(words) + 1, line.region.y)


async def test_clicking_the_filter_words_does_what_the_keys_do(clock: VirtualClock) -> None:
    app = app_with(clock, MIXED)
    async with app.run_test(size=(120, 30)) as pilot:
        tab = await settle(app, pilot)
        await pilot.click(offset=click_offset(app, "bots hidden"))
        await pilot.pause()
        assert tab.hide_bots is False
        await pilot.click(offset=click_offset(app, "articles only"))
        await pilot.pause()
        assert tab.articles_only is False
        await pilot.click(offset=click_offset(app, "any size"))
        await pilot.pause()
        assert tab.min_bytes == 100
        await pilot.click(offset=click_offset(app, "live"))
        await pilot.pause()
        assert tab.paused is True
        await pilot.click(offset=click_offset(app, "paused"))
        await pilot.pause()
        assert tab.paused is False
        await pilot.click(offset=click_offset(app, "enwiki"))
        await pilot.pause()
        assert isinstance(app.screen, WikiPrompt)


async def test_the_log_is_capped_at_500_rows_and_drops_the_oldest(clock: VirtualClock) -> None:
    edits = [make_edit(f"Page {n}") for n in range(600)]
    app = app_with(clock, edits)
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        table = app.query_one("#log", DataTable)
        assert table.row_count == 500
        assert tab.row_titles()[0] == "Page 599"
        assert tab.row_titles()[-1] == "Page 100"


async def test_new_edits_wait_for_the_quarter_second_flush(clock: VirtualClock) -> None:
    app = app_with(clock, [])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        for n in range(30):
            tab.accept(make_edit(f"Burst {n}"))
        assert app.query_one("#log", DataTable).row_count == 0  # queued, not drawn yet
        tab.flush_log()
        await pilot.pause()
        assert app.query_one("#log", DataTable).row_count == 30


async def test_edits_of_other_wikis_and_other_items_are_ignored(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("Elsewhere", wiki="dewiki"), make_edit("Here")])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        assert log_titles(app) == ["Here"]
        assert tab.stats.total == 1


async def test_pause_holds_the_log_while_the_numbers_keep_counting(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("Before")])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        await pilot.press("p")
        assert tab.paused
        assert filters_text(app).endswith("paused")
        tab.accept(make_edit("During one"))
        tab.accept(make_edit("During two", bot=True))
        tab.flush_log()
        await pilot.pause()
        assert log_titles(app) == ["Before"]
        assert tab.stats.total == 3  # the numbers did not stop
        await pilot.press("p")
        await pilot.pause()
        assert log_titles(app) == ["During one", "Before"]  # the bot edit is filtered, as ever
        assert filters_text(app).endswith("live")


async def test_a_filter_change_while_paused_does_not_show_newer_edits(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("Before")])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        await pilot.press("p")
        tab.accept(make_edit("During"))
        await pilot.press("m")
        await pilot.pause()
        assert log_titles(app) == ["Before"]


async def test_the_cursor_stays_on_its_row_as_new_rows_arrive(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit(f"Page {n}") for n in range(5)])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        await pilot.press("down", "down")  # on "Page 2"
        table = app.query_one("#log", DataTable)
        assert tab.row_titles()[table.cursor_row] == "Page 2"
        tab.accept(make_edit("Newer"))
        tab.flush_log()
        await pilot.pause()
        assert tab.row_titles()[table.cursor_row] == "Page 2"


# --- widths ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "labels"),
    [
        (80, ["Time", "Bytes", "Article"]),
        (120, ["Time", "Bytes", "Article", "Editor"]),
        (160, ["Time", "Bytes", "Article", "Editor", "Summary"]),
    ],
)
async def test_columns_follow_the_width_and_nothing_scrolls_sideways(
    clock: VirtualClock, width: int, labels: list[str]
) -> None:
    long = make_edit("A" * 200, user="U" * 60, comment="C" * 400)
    app = app_with(clock, [long])
    async with app.run_test(size=(width, 30)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#log", DataTable)
        assert [str(c.label) for c in table.ordered_columns] == labels
        assert table.max_scroll_x == 0
        assert str(table.get_cell(table.ordered_rows[0].key, "Article")).endswith("…")


async def test_columns_change_when_the_window_is_resized(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit()])
    async with app.run_test(size=(80, 30)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#log", DataTable)
        assert len(table.ordered_columns) == 3
        await pilot.resize_terminal(150, 30)
        await pilot.pause()
        assert len(table.ordered_columns) == 5
        assert table.row_count == 1  # the rows came back
        await pilot.resize_terminal(80, 30)
        await pilot.pause()
        assert len(table.ordered_columns) == 3


# --- the side column -------------------------------------------------------------------------------------------------


async def test_side_column_shows_the_numbers(clock: VirtualClock) -> None:
    edits = [
        make_edit("Hot", user="A"),
        make_edit("Hot", user="B"),
        make_edit("Warm", bot=True),
        make_edit("Talk:X", namespace=1),
    ]
    app = app_with(clock, edits)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert str(app.query_one("#epm", Static).render()) == "Edits per minute  4"
        assert "People 75%" in str(app.query_one("#split", Static).render())
        assert "Bots 25%" in str(app.query_one("#split", Static).render())
        top = app.query_one("#top", DataTable)
        rows = [(str(top.get_cell(r.key, "Edits")), str(top.get_cell(r.key, "Article"))) for r in top.ordered_rows]
        assert rows == [("2", "Hot"), ("1", "Warm")]
        spark = app.query_one("#spark", Sparkline)
        assert len(spark.data or []) == 60
        assert spark.data[-1] == 4


async def test_the_split_counts_bots_even_while_they_are_hidden(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("A", bot=True), make_edit("B", bot=True)])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert log_titles(app) == []
        assert "Bots 100%" in str(app.query_one("#split", Static).render())


async def test_the_side_column_before_any_edit(clock: VirtualClock) -> None:
    app = app_with(clock, [])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert "No edits yet" in str(app.query_one("#split", Static).render())
        assert str(app.query_one("#epm", Static).render()) == "Edits per minute  0"


async def test_the_store_gets_the_counts_once_a_minute(clock: VirtualClock) -> None:
    store = Store(":memory:")
    app = app_with(clock, [make_edit("Ada Lovelace", delta=40)], store=store)
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        minute = clock.now().strftime("%Y-%m-%dT%H:%M")
        assert store.edit_minute("enwiki", "Ada Lovelace", minute) is None
        clock.advance(61)
        tab.refresh_side()
        assert store.edit_minute("enwiki", "Ada Lovelace", minute) == (1, 1, 40, 0)


# --- another wiki ----------------------------------------------------------------------------------------------------


async def test_i_switches_wiki_and_starts_the_numbers_over(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("English"), make_edit("Deutsch", wiki="dewiki", server="de.wikipedia.org")])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        assert log_titles(app) == ["English"]
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, WikiPrompt)
        await pilot.press(*"dewiki", "enter")
        await pilot.pause()
        assert tab.wiki == "dewiki"
        assert app.wiki == "dewiki"
        assert log_titles(app) == []
        assert tab.stats.total == 0
        assert filters_text(app).startswith("dewiki")
        tab.accept(make_edit("Neu", wiki="dewiki"))
        tab.flush_log()
        await pilot.pause()
        assert log_titles(app) == ["Neu"]


@pytest.mark.parametrize("bad", ["", "nope", "en wiki", "enwiki x", "enwikix", "enwiki[/]", "EN1wiki", "wikipedia"])
async def test_the_wiki_dialog_refuses_bad_names(clock: VirtualClock, bad: str) -> None:
    app = app_with(clock, [])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("i")
        await pilot.pause()
        app.screen.query_one(Input).value = bad
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, WikiPrompt)
        assert "wiki name" in str(app.screen.query_one("#wiki-error", Static).render())


async def test_the_wiki_dialog_cancels_with_escape(clock: VirtualClock) -> None:
    app = app_with(clock, [])
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("i")
        await pilot.press(*"dewiki", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, WikiPrompt)
        assert live_tab(app).wiki == "enwiki"


async def test_choosing_the_same_wiki_changes_nothing(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("Stay")])
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        await pilot.press("i")
        await pilot.press(*"enwiki", "enter")
        await pilot.pause()
        assert log_titles(app) == ["Stay"]
        assert tab.stats.total == 1


# --- opening a row ---------------------------------------------------------------------------------------------------


async def test_enter_on_a_log_row_opens_the_detail_with_the_article_link(clock: VirtualClock) -> None:
    edit = make_edit("Ada Lovelace (née Byron)", user="Alice", delta=-42, comment="trim", kind="edit")
    app = app_with(clock, [edit])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, EditDetail)
        assert detail.url == "https://en.wikipedia.org/wiki/Ada_Lovelace_(n%C3%A9e_Byron)"
        text = " ".join(str(label.render()) for label in detail.query("Label"))
        for part in (
            "Ada Lovelace (née Byron)",
            "enwiki",
            "Alice",
            "Sat 07 Nov 2026 14:41:05",
            "-42",
            "trim",
            detail.url,
        ):
            assert part in text
        link = detail.query_one("#link").render()
        assert any(getattr(span.style, "link", None) == detail.url for span in link.spans)  # type: ignore[attr-defined]
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, EditDetail)


async def test_the_detail_closes_with_its_button(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit()])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click("#close")
        await pilot.pause()
        assert not isinstance(app.screen, EditDetail)


async def test_clicking_a_log_row_opens_its_detail(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("First"), make_edit("Second")])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.click("#log", offset=(10, 2))
        await pilot.pause()
        await pilot.click("#log", offset=(10, 2))
        await pilot.pause()
        assert isinstance(app.screen, EditDetail)


async def test_enter_on_a_top_row_opens_the_detail_of_its_latest_edit(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("Hot", user="First"), make_edit("Hot", user="Second", server="en.wikipedia.org")])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        app.query_one("#top").focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, EditDetail)
        assert detail.url == "https://en.wikipedia.org/wiki/Hot"
        assert "Second" in " ".join(str(label.render()) for label in detail.query("Label"))


async def test_there_is_no_link_without_an_edit_or_a_trusted_host(clock: VirtualClock) -> None:
    detail = EditDetail("dewiki", "Ada Lovelace", None, EASTERN)
    assert detail.url is None
    assert EditDetail("enwiki", "A", make_edit(server=""), EASTERN).url is None


async def test_b_hides_and_shows_bot_edits(clock: VirtualClock) -> None:
    app = app_with(clock, MIXED, config=Config(hide_bots=True))
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert "Bot article" not in log_titles(app)
        await pilot.press("b")
        await pilot.pause()
        assert "Bot article" in log_titles(app)
        assert "bots shown" in filters_text(app)
        await pilot.press("b")
        await pilot.pause()
        assert "Bot article" not in log_titles(app)


async def test_tab_moves_the_focus_between_the_log_and_the_top_table(clock: VirtualClock) -> None:
    app = app_with(clock, [make_edit("One"), make_edit("Two")])
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert app.focused is app.query_one("#log")
        await pilot.press("tab")
        assert app.focused is app.query_one("#top")
        await pilot.press("shift+tab")
        assert app.focused is app.query_one("#log")
        await pilot.press("tab", "tab", "tab")  # log, top, tab strip, and round to the log again
        assert app.focused is app.query_one("#log")
