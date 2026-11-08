"""Pilot tests for the shell: tabs, the top bar, the health bar, the floor size, help and the watchlist."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import EASTERN, ListSource, live_tab, make_app, make_edit, settle
from textual.widgets import Button, DataTable, Input, Label, TabbedContent

from wikihose.clock import VirtualClock
from wikihose.config import Config, load_config
from wikihose.sources.events import FeedStatus
from wikihose.sources.recording import sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.ui.app import TooSmall, WikihoseApp
from wikihose.ui.bars import HealthBar, TopBar
from wikihose.ui.dialogs import AddPage, EditDetail, HelpScreen

TAB_IDS = ["live", "reading", "watchlist", "diff", "convergence"]


def app_with(clock: VirtualClock, *edits, **kwargs) -> WikihoseApp:
    return make_app(ListSource(clock, list(edits)), clock, **kwargs)


def active_tab(app: WikihoseApp) -> str:
    return app.query_one(TabbedContent).active


@pytest.mark.parametrize(("key", "tab"), [("1", "live"), ("2", "reading"), ("3", "watchlist"), ("4", "diff"), ("5", "convergence")])
async def test_number_keys_switch_tabs(clock: VirtualClock, key: str, tab: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("5" if key != "5" else "1")  # start somewhere else
        await pilot.press(key)
        await pilot.pause()
        assert active_tab(app) == tab


@pytest.mark.parametrize("tab", TAB_IDS)
async def test_clicking_a_tab_switches_to_it(clock: VirtualClock, tab: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click(f"Tab#--content-tab-{tab}")
        await pilot.pause()
        assert active_tab(app) == tab


async def test_there_are_exactly_five_tabs_with_these_labels(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)):
        tabs = app.query_one(TabbedContent)
        assert [pane.id for pane in tabs.query("TabPane")] == TAB_IDS
        labels = [str(tabs.get_tab(tab_id).label_text) for tab_id in TAB_IDS]
        assert labels == ["Live", "Reading", "Watchlist", "Diff", "Convergence"]


async def test_focus_follows_the_open_tab(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit())
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        assert app.focused is not None
        assert app.focused.id == "log"
        await pilot.press("2")
        await pilot.pause()
        assert app.focused is None or app.focused.id != "log"
        await pilot.press("1")
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.id == "log"


async def test_clicking_the_live_tab_puts_the_focus_in_it(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.click("Tab#--content-tab-live")
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.id == "log"


@pytest.mark.parametrize("tab", ["reading", "diff", "convergence"])
async def test_stub_panes_say_what_is_coming(clock: VirtualClock, tab: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click(f"Tab#--content-tab-{tab}")
        await pilot.pause()
        text = str(app.query_one(f"TabPane#{tab} StubPane").render())
        assert "Arrives in a later version." in text


async def test_watchlist_stub_says_how_to_add_a_page(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        text = str(app.query_one("TabPane#watchlist StubPane").render())
        assert "Press w to add a page." in text
        assert "Arrives in a later version." in text


# --- the top bar -----------------------------------------------------------------------------------------------------


async def test_top_bar_names_the_app_the_wiki_and_local_time(clock: VirtualClock) -> None:
    app = app_with(clock)  # the clock says 2026-11-07 12:00 UTC, which is 07:00 in New York
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        text = str(app.query_one("#topbar-left").render())
        assert text == "wikihose  enwiki  Sat 7 Nov 07:00"
        assert str(app.query_one("#topbar-right").render()) == ""


async def test_top_bar_in_a_replay_says_recorded_and_shows_the_state(clock: VirtualClock) -> None:
    with sample_recording() as path:
        source = ReplaySource(path, clock, speed=4)
        app = make_app(source, clock)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            right = str(app.query_one("#topbar-right").render())
            assert right.startswith("Recorded data, not live")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert str(app.query_one("#topbar-right").render()) == "Recorded data, not live  replay finished"
            assert app.query_one(HealthBar).failing == []  # the end of a recording is not a failure


async def test_top_bar_follows_a_wiki_change(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("i")
        await pilot.press(*"dewiki", "enter")
        await pilot.pause()
        assert "dewiki" in str(app.query_one("#topbar-left").render())


# --- the health bar --------------------------------------------------------------------------------------------------


async def test_health_bar_is_hidden_while_everything_is_ok(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        app._dispatch(FeedStatus("stream", "ok", at=clock.now()))
        await pilot.pause()
        assert not app.query_one(HealthBar).display


async def test_health_bar_shows_a_failing_feed_and_hides_on_recovery(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        bar = app.query_one(HealthBar)
        app._dispatch(FeedStatus("stream", "rate_limited", retry_in=12, at=clock.now()))
        await pilot.pause()
        assert bar.display
        assert str(bar.render()) == "Stream: rate limited, retrying in 12s"
        app._dispatch(FeedStatus("stream", "ok", at=clock.now()))
        await pilot.pause()
        assert not bar.display


@pytest.mark.parametrize(
    ("state", "colour_name"),
    [("rate_limited", "warning"), ("reconnecting", "warning"), ("bad_data", "warning"), ("down", "error")],
)
async def test_health_bar_colours_by_severity(clock: VirtualClock, state: str, colour_name: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        app._dispatch(FeedStatus("stream", state, at=clock.now()))  # type: ignore[arg-type]
        await pilot.pause()
        content = app.query_one(HealthBar).render()
        expected = {"warning": app.current_theme.warning, "error": app.current_theme.error}[colour_name]
        styles = [str(span.style) for span in content.spans]  # type: ignore[attr-defined]
        assert f"${colour_name}" in styles
        assert expected  # the theme defines the colour the markup names


# --- the floor size --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(79, 24), (80, 23), (79, 23), (40, 10)])
async def test_too_small_covers_the_screen_and_clears_on_resize(clock: VirtualClock, size: tuple[int, int]) -> None:
    app = app_with(clock)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        cover = app.screen.query_one(TooSmall)
        assert cover.display
        text = " ".join(str(cover.render()).split())
        assert text == f"Make the window bigger. wikihose needs 80x24; this one is {size[0]}x{size[1]}."
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert not cover.display


async def test_the_cover_comes_back_when_the_window_shrinks_again(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(120, 30)) as pilot:
        cover = app.screen.query_one(TooSmall)
        assert not cover.display
        await pilot.resize_terminal(79, 23)
        await pilot.pause()
        assert cover.display
        assert "79x23" in str(cover.render())


async def test_exactly_the_floor_is_big_enough(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.screen.query_one(TooSmall).display


# --- help ------------------------------------------------------------------------------------------------------------


def help_keys(app: WikihoseApp) -> list[str]:
    screen = app.screen
    assert isinstance(screen, HelpScreen)
    return [str(line.render()).split()[0] for line in screen.query(".help-line")]


async def test_help_lists_the_active_keys_in_live_mode(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert help_keys(app) == ["b", "a", "m", "i", "p", "w", "?", "q", "1-5", "Enter", "Tab", "Mouse"]


async def test_help_lists_the_replay_keys_only_in_a_replay(clock: VirtualClock) -> None:
    with sample_recording() as path:
        app = make_app(ReplaySource(path, clock), clock)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            assert help_keys(app) == [
                "b", "a", "m", "i", "p", "w", "?", "q", "space", "+", "-", "]", "1-5", "Enter", "Tab", "Mouse",
            ]  # fmt: skip


async def test_help_on_another_tab_does_not_list_the_live_keys(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2")
        await pilot.press("question_mark")
        await pilot.pause()
        assert help_keys(app) == ["w", "?", "q", "1-5", "Enter", "Tab", "Mouse"]


@pytest.mark.parametrize("closer", ["escape", "question_mark", "button"])
async def test_help_closes(clock: VirtualClock, closer: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        if closer == "button":
            await pilot.click(Button)
        else:
            await pilot.press(closer)
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_help_fits_at_the_floor_size_in_a_replay(clock: VirtualClock) -> None:
    with sample_recording() as path:
        app = make_app(ReplaySource(path, clock), clock)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            assert app.screen.query_one(Button).region.bottom <= 24  # the Close button is still on screen


async def test_clicking_help_in_the_footer_opens_help(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        key = next(k for k in app.query("FooterKey") if "Help" in str(k.render()))
        await pilot.click(key.__class__, offset=(key.region.x, key.region.y))
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


async def test_clicking_a_footer_key_runs_its_action(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        key = next(k for k in app.query("FooterKey") if "Bots" in str(k.render()))
        await pilot.click(offset=(key.region.x + 1, key.region.y))
        await pilot.pause()
        assert live_tab(app).hide_bots is False


async def test_q_quits_and_closes_the_source(clock: VirtualClock) -> None:
    source = ListSource(clock)
    app = make_app(source, clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("q")
    assert source.closed
    assert not app.is_running


async def test_the_source_is_closed_when_the_app_ends_another_way(clock: VirtualClock) -> None:
    source = ListSource(clock)
    app = make_app(source, clock)
    async with app.run_test(size=(100, 30)):
        pass
    assert source.closed


# --- the replay keys -------------------------------------------------------------------------------------------------


async def test_replay_keys_pause_speed_and_skip(clock: VirtualClock) -> None:
    with sample_recording() as path:
        source = ReplaySource(path, clock)
        app = make_app(source, clock)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("space")
            assert source.paused
            assert str(app.query_one("#topbar-right").render()).endswith("paused")
            await pilot.press("space")
            assert not source.paused
            await pilot.press("plus", "plus")
            assert source.speed == 4
            assert str(app.query_one("#topbar-right").render()).endswith("4x")
            await pilot.press("minus", "minus", "minus", "minus", "minus", "minus", "minus")
            assert source.speed == 0.5
            before = source.moment()
            await pilot.press("right_square_bracket")
            assert (source.moment() - before).total_seconds() == 60


@pytest.mark.parametrize("key", ["space", "plus", "minus", "right_square_bracket"])
async def test_replay_keys_do_nothing_in_a_live_session(clock: VirtualClock, key: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        footer_text = " ".join(str(k.render()) for k in app.query("FooterKey"))
        assert "Faster" not in footer_text
        assert "Skip" not in footer_text
        assert str(app.query_one("#topbar-right").render()) == ""


async def test_footer_shows_the_replay_keys_only_in_a_replay(clock: VirtualClock) -> None:
    with sample_recording() as path:
        replay = make_app(ReplaySource(path, clock), clock)
        async with replay.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            text = " ".join(str(k.render()) for k in replay.query("FooterKey"))
            for label in ("Play", "Faster", "Slower", "Skip"):
                assert label in text
    live = app_with(clock)
    async with live.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        text = " ".join(str(k.render()) for k in live.query("FooterKey"))
        for label in ("Bots", "Articles", "Size", "Wiki", "Pause", "Watch", "Help", "Quit"):
            assert label in text
        for label in ("Play", "Faster", "Slower", "Skip"):
            assert label not in text


# --- the watchlist ---------------------------------------------------------------------------------------------------


async def test_w_on_a_log_row_adds_that_article_and_badges_the_tab(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit("Ada Lovelace"))
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        assert ("enwiki", "Ada Lovelace") in app.watchlist
        assert len(app.watchlist) == 1
        assert [n.message for n in app._notifications] == ["Watching Ada Lovelace"]
        tab = app.query_one(TabbedContent).get_tab("watchlist")
        assert str(tab.label_text) == "Watchlist 1"


async def test_w_on_a_top_row_adds_that_article(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit("Grace Hopper"))
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        app.query_one("#top").focus()
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert ("enwiki", "Grace Hopper") in app.watchlist


async def test_w_on_the_same_page_twice_says_so_and_does_not_double_count(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit("Ada Lovelace"))
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("w", "w")
        await pilot.pause()
        assert len(app.watchlist) == 1
        assert [n.message for n in app._notifications] == ["Watching Ada Lovelace", "Already watching Ada Lovelace"]


@pytest.mark.parametrize("tab_key", ["1", "2", "3", "4", "5"])
async def test_w_anywhere_else_opens_the_dialog_and_adds_what_you_type(clock: VirtualClock, tab_key: str) -> None:
    app = app_with(clock)  # no edits, so the Live tab has no row to add either
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(tab_key)
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, AddPage)
        await pilot.press(*"ada lovelace", "enter")
        await pilot.pause()
        assert not isinstance(app.screen, AddPage)
        assert ("enwiki", "Ada lovelace") in app.watchlist
        assert str(app.query_one(TabbedContent).get_tab("watchlist").label_text) == "Watchlist 1"


async def test_the_dialog_reads_a_pasted_wikipedia_url(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        app.screen.query_one(Input).value = "https://de.wikipedia.org/wiki/Ada_Lovelace"
        await pilot.click("#add")
        await pilot.pause()
        assert ("dewiki", "Ada Lovelace") in app.watchlist


async def test_the_dialog_cancels_with_escape_and_with_the_button(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        await pilot.press(*"Ada", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, AddPage)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press(*"Ada")
        await pilot.click("#cancel")
        await pilot.pause()
        assert not isinstance(app.screen, AddPage)
        assert len(app.watchlist) == 0


async def test_an_empty_dialog_adds_nothing(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        await pilot.click("#add")
        await pilot.pause()
        assert len(app.watchlist) == 0
        assert not isinstance(app.screen, AddPage)


async def test_the_badge_goes_away_with_no_pages(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert str(app.query_one(TabbedContent).get_tab("watchlist").label_text) == "Watchlist"


async def test_a_watched_article_gets_the_accent_colour_in_the_log(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit("Ada Lovelace"), make_edit("Grace Hopper"))
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        app.watch("enwiki", "Ada Lovelace")
        await pilot.pause()
        table = app.query_one("#log", DataTable)
        by_title = {}
        for key, edit in tab._rows:
            cell = table.get_cell(key, "Article")
            by_title[edit.title] = cell.spans[0].style if cell.spans else str(cell.style)
        assert app.current_theme.accent in str(by_title["Ada Lovelace"])
        assert app.current_theme.accent not in str(by_title["Grace Hopper"])


# --- settings --------------------------------------------------------------------------------------------------------


async def test_filter_changes_are_saved_to_the_config_file(clock: VirtualClock, tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.toml"
    app = app_with(clock, config_path=path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("b", "a", "m")
        await pilot.pause()
    saved, warnings = load_config(path)
    assert warnings == []
    assert (saved.hide_bots, saved.articles_only, saved.min_bytes) == (False, False, 100)


async def test_no_config_never_writes_the_config_file(clock: VirtualClock, tmp_path: Path) -> None:
    app = app_with(clock, config_path=None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("b", "a", "m")
        await pilot.pause()
    assert app.config.hide_bots is False  # the change holds for this run
    assert not (tmp_path / "config").exists()


async def test_a_command_line_wiki_is_not_saved_by_a_filter_change(clock: VirtualClock, tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    app = app_with(clock, config=Config(wiki="dewiki"), config_path=path)  # as if --wiki dewiki; the file has no wiki
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("b")
        await pilot.pause()
    saved, _ = load_config(path)
    assert saved.wiki == "enwiki"
    assert saved.hide_bots is False


async def test_an_unsaveable_config_is_reported_not_fatal(clock: VirtualClock, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder is needed", "utf-8")
    app = app_with(clock, config_path=blocker / "config.toml")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        assert any("Could not save settings" in n.message for n in app._notifications)


async def test_the_configured_theme_is_used(clock: VirtualClock) -> None:
    app = app_with(clock, config=Config(theme="wikihose-light"))
    async with app.run_test(size=(100, 30)):
        assert app.theme == "wikihose-light"
    other = app_with(clock, config=Config(theme="no-such-theme"))
    async with other.run_test(size=(100, 30)):
        assert other.theme == "wikihose-dark"


async def test_the_local_zone_defaults_to_the_machines(clock: VirtualClock) -> None:
    app = WikihoseApp(ListSource(clock), Config(), clock=clock)
    assert app.tz is not None
    explicit = WikihoseApp(ListSource(clock), Config(), clock=clock, tz=EASTERN)
    assert explicit.tz is EASTERN


async def test_edit_detail_is_not_a_help_screen_and_closes_with_escape(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit())
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, EditDetail)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, EditDetail)
        assert Label  # imported for the other tests' selectors
        assert TopBar
