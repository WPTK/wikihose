"""Text from the stream is written by strangers: it must not reach the terminal as markup, links or escape codes."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from support import ListSource, live_tab, make_app, make_edit, settle, wait_for_stream
from textual.widgets import DataTable

from wikihose.cli import main
from wikihose.clock import VirtualClock
from wikihose.config import Config, is_valid_wiki
from wikihose.sources.events import Edit, clean_text, parse_recentchange
from wikihose.sources.replay import ReplaySource
from wikihose.titles import article_url, valid_host
from wikihose.ui.dialogs import EditDetail
from wikihose.ui.tabs.live import FilterLine

HEADER = (
    '{"format":"wikihose-recording","version":1,"recorded_at":"2026-11-07T12:00:00+00:00",'
    '"stream":"recentchange","wiki":"all"}'
)


def raw_edit(**changes: Any) -> str:
    data: dict[str, Any] = {
        "type": "edit",
        "namespace": 0,
        "title": "Ada Lovelace",
        "comment": "fix",
        "timestamp": 1794098367,
        "user": "Someone",
        "bot": False,
        "length": {"old": 100, "new": 130},
        "server_name": "en.wikipedia.org",
        "wiki": "enwiki",
    }
    data.update(changes)
    return json.dumps(data)


def parsed(**changes: Any) -> Edit:
    edit = parse_recentchange(raw_edit(**changes))
    assert isinstance(edit, Edit)
    return edit


# --- cleaning --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("plain", "plain"),
        ("a\x1b[2Jb", "a�[2Jb"),
        ("a\x00b\x07c\x7fd\x9be", "a�b�c�d�e"),
        ("line\nbreak\r", "line�break�"),
        ("tab\there", "tab here"),
        ("a‮b‪c⁦d⁩e", "abcde"),
        ("né 日本語 ✓", "né 日本語 ✓"),
    ],
)
def test_clean_text(text: str, clean: str) -> None:
    assert clean_text(text) == clean


def test_a_parsed_edit_has_clean_user_title_comment_and_wiki() -> None:
    edit = parsed(user="Al\x1b]8;;x\x1b\\ice‮", title="T\x1b[1mt", comment="\x1b[2J", wiki="en\x1bwiki")
    for text in (edit.user, edit.title, edit.comment, edit.wiki):
        assert "\x1b" not in text
        assert "‮" not in text
    assert edit.user == "Al�]8;;x�\\ice"


# --- the link host ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["en.wikipedia.org", "de.m.wikipedia.org", "commons.wikimedia.org", "www.wikidata.org", "fr.wiktionary.org"]
)
def test_wikimedia_hosts_are_accepted(host: str) -> None:
    assert valid_host(host)
    assert parsed(server_name=host).server_name == host


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.example.com",
        "en.wikipedia.org.evil.com",
        "EN.wikipedia.org",
        "en.wikipedia.org/",
        "en.wikipedia.org\n",
        "en.wikipedia.org\x1b\\",
        "evil.com/en.wikipedia.org",
        "user@en.wikipedia.org",
        "en.wikipedia.org:8080",
        ".wikipedia.org",
    ],
)
def test_other_hosts_are_dropped_at_parse_time(host: str) -> None:
    assert not valid_host(host)
    assert parsed(server_name=host).server_name == ""
    assert article_url(host, "Ada") is None


async def test_a_hostile_server_name_gives_no_link(clock: VirtualClock) -> None:
    edit = parsed(server_name="en.wikipedia.org\x1b\\\x1b]8;;http://evil.example\x1b\\")
    app = make_app(ListSource(clock, [edit]), clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, EditDetail)
        assert detail.url is None
        assert not detail.query("#link")


async def test_a_replayed_edit_with_an_escape_code_renders_without_it(clock: VirtualClock) -> None:
    edit = parsed(comment="\x1b[2Jboom", user="Mal\x1b[31mlory", title="Hot\x1b[2J")
    app = make_app(ListSource(clock, [edit]), clock)
    async with app.run_test(size=(240, 40)) as pilot:
        await settle(app, pilot)
        table = app.query_one("#log", DataTable)
        cells = [str(table.get_cell(row.key, col.key)) for row in table.ordered_rows for col in table.ordered_columns]
        assert any("boom" in cell for cell in cells)
        assert not any("\x1b" in cell for cell in cells)
        await pilot.press("enter")
        await pilot.pause()
        text = " ".join(str(label.render()) for label in app.screen.query("Label"))
        assert "boom" in text
        assert "\x1b" not in text


# --- markup ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("title", ["Foo [/]", "[@click=app.quit]x", "[bold]b"])
async def test_toasts_show_a_title_as_plain_text(clock: VirtualClock, title: str) -> None:
    app = make_app(ListSource(clock, []), clock)
    async with app.run_test(size=(100, 30)) as pilot:
        app.watch_page("enwiki", title)
        app.watch_page("enwiki", title)
        await pilot.pause()
        notes = list(app._notifications)
        assert len(notes) == 2
        assert all(note.markup is False for note in notes)
        assert title in notes[0].message
        assert app.is_running


async def test_the_filter_line_survives_markup_in_the_wiki_name(clock: VirtualClock) -> None:
    app = make_app(ListSource(clock, []), clock, config=Config(wiki="a[/]b[@click=app.quit]wiki"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert str(app.query_one("#filters").render()).startswith("a[/]b[@click=app.quit]wiki")


async def test_an_unknown_filter_word_does_nothing(clock: VirtualClock) -> None:
    app = make_app(ListSource(clock, []), clock)
    async with app.run_test(size=(100, 30)) as pilot:
        tab = live_tab(app)
        before = (tab.hide_bots, tab.articles_only, tab.min_bytes, tab.paused)
        tab.on_filter_line_picked(FilterLine.Picked("nope"))
        await pilot.pause()
        assert (tab.hide_bots, tab.articles_only, tab.min_bytes, tab.paused) == before


# --- the wiki name ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["enwiki", "dewiki", "zh_min_nanwiki"])
def test_good_wiki_names(name: str) -> None:
    assert is_valid_wiki(name)


@pytest.mark.parametrize("name", ["", "enwikix", "enwiki[/]", "enwiki x", "enwiki\n", "EN1wiki", "wiki x"])
def test_bad_wiki_names(name: str) -> None:
    assert not is_valid_wiki(name)


@pytest.mark.parametrize("name", ["enwikix", "enwiki[/]", "enwiki\n"])
def test_the_wiki_flag_refuses_bad_names(name: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--wiki", name, "--no-config"])
    assert exit_info.value.code == 2


# --- the log keeps its scroll position -------------------------------------------------------------------------------


async def test_new_edits_do_not_move_the_scroll_position(clock: VirtualClock) -> None:
    edits = [make_edit(f"Page {n}") for n in range(200)]
    app = make_app(ListSource(clock, edits), clock)
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        table = app.query_one("#log", DataTable)
        table.scroll_to(y=40, animate=False, immediate=True)
        await pilot.pause()
        assert table.scroll_y == 40
        keep = tab.row_titles()[45]
        table.move_cursor(row=45, animate=False, scroll=False)
        for n in range(5):
            tab.accept(make_edit(f"New {n}"))
        tab.flush_log()
        await pilot.pause()
        await pilot.pause()
        assert table.scroll_y == 40
        assert tab.row_titles()[0] == "New 4"
        assert tab.row_titles()[table.cursor_row] == keep


# --- a burst of events does not starve the loop ----------------------------------------------------------------------


def burst_file(tmp_path: Path, count: int) -> Path:
    path = tmp_path / "burst.jsonl.gz"
    lines = [HEADER] + [json.dumps({"t": 0.0, "id": str(n), "data": raw_edit(title=f"P{n}")}) for n in range(count)]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


class CountingClock(VirtualClock):
    sleeps = 0

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        await super().sleep(seconds)


async def test_a_burst_gives_the_event_loop_a_turn_every_so_often(clock: VirtualClock, tmp_path: Path) -> None:
    counting = CountingClock(clock.now())
    source = ReplaySource(burst_file(tmp_path, 1000), counting)
    items = [item async for item in source.items()]
    assert len(items) == 1002
    assert counting.sleeps >= 9  # one turn per 100 items


async def test_a_ten_thousand_event_burst_still_lets_a_key_through(clock: VirtualClock, tmp_path: Path) -> None:
    source = ReplaySource(burst_file(tmp_path, 10_000), clock)
    app = make_app(source, clock)
    async with app.run_test(size=(100, 30)) as pilot:
        tab = live_tab(app)
        seen: list[int] = []
        original = tab.action_toggle_pause

        def spy() -> None:
            seen.append(tab.stats.total)  # how much of the burst had been played when the key was handled
            original()

        tab.action_toggle_pause = spy  # type: ignore[method-assign]
        await pilot.press("p")
        await wait_for_stream(app, pilot)
        assert tab.paused
        assert seen
        assert seen[0] < 10_000
        assert tab.stats.total == 10_000
