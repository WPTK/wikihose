"""Pilot tests for the shell: tabs, the top bar, the health bar, the floor size, help and the watchlist."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from support import (
    EASTERN,
    ListSource,
    action_api,
    finish_work,
    footer_keys,
    footer_text,
    live_tab,
    make_app,
    make_edit,
    settle,
    wait_for_stream,
)
from textual.widgets import Button, DataTable, Input, Label, TabbedContent

from wikihose.clock import VirtualClock
from wikihose.config import Config, load_config
from wikihose.net import Client
from wikihose.sources.events import FeedStatus
from wikihose.sources.recording import sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.ui.app import TooSmall, WikihoseApp
from wikihose.ui.bars import HealthBar, TopBar
from wikihose.ui.dialogs import AddPage, EditDetail, HelpScreen, WikiPrompt

TAB_IDS = ["live", "reading", "watchlist", "diff", "convergence"]


def app_with(clock: VirtualClock, *edits, **kwargs) -> WikihoseApp:
    return make_app(ListSource(clock, list(edits)), clock, **kwargs)


def active_tab(app: WikihoseApp) -> str:
    return app.query_one(TabbedContent).active


@pytest.mark.parametrize(
    ("key", "tab"), [("1", "live"), ("2", "reading"), ("3", "watchlist"), ("4", "diff"), ("5", "convergence")]
)
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


@pytest.mark.parametrize("tab", ["convergence"])
async def test_stub_panes_say_what_is_coming(clock: VirtualClock, tab: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click(f"Tab#--content-tab-{tab}")
        await pilot.pause()
        text = str(app.query_one(f"TabPane#{tab} StubPane").render())
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
            await wait_for_stream(app, pilot)
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
        assert help_keys(app) == ["b", "a", "m", "i", "d", "p", "w", "W", "?", "q", "1-5", "Enter", "Tab", "Mouse"]


async def test_help_lists_the_replay_keys_only_in_a_replay(clock: VirtualClock) -> None:
    with sample_recording() as path:
        app = make_app(ReplaySource(path, clock), clock)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            assert help_keys(app) == [
                "b", "a", "m", "i", "d", "p", "w", "W", "?", "q", "space", "+", "-", "]", "1-5", "Enter", "Tab", "Mouse",
            ]  # fmt: skip


async def test_help_on_another_tab_does_not_list_the_live_keys(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("3")
        await pilot.press("question_mark")
        await pilot.pause()
        assert help_keys(app) == ["w", "W", "?", "q", "1-5", "Enter", "Tab", "Mouse"]


async def test_an_empty_watchlist_tab_takes_no_focus(clock: VirtualClock) -> None:
    """Its table is hidden, so the focus stays off it and the tab's m, d and Delete keys do not reach the help screen."""
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("3")
        app.screen.focus_active_pane()
        await pilot.pause()
        assert app.focused is None


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
        await click_footer(pilot, app, "Help")
        assert isinstance(app.screen, HelpScreen)


async def test_clicking_a_footer_key_runs_its_action(clock: VirtualClock) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await click_footer(pilot, app, "Bots")
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
        source.pause()  # a replay on a virtual clock would otherwise race to its end
        app = make_app(source, clock)
        async with app.run_test(size=(100, 30)) as pilot:
            assert str(app.query_one("#topbar-right").render()).endswith("paused")
            await pilot.press("plus", "plus")
            assert source.speed == 4
            await pilot.press("minus", "minus", "minus", "minus", "minus", "minus", "minus")
            assert source.speed == 0.5
            before = source.moment()
            await pilot.press("right_square_bracket")
            assert (source.moment() - before).total_seconds() == 60
            await pilot.press("space")
            assert not source.paused
            await wait_for_stream(app, pilot)  # it plays on to the end, since the skip passed the last event
            assert source.moment() >= source.recorded_start


@pytest.mark.parametrize("key", ["space", "plus", "minus", "right_square_bracket"])
async def test_replay_keys_do_nothing_in_a_live_session(clock: VirtualClock, key: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        shown = footer_text(await footer_keys(pilot, app, needing="Quit"))
        assert "Faster" not in shown
        assert "Skip" not in shown
        assert str(app.query_one("#topbar-right").render()) == ""


async def test_footer_shows_the_replay_keys_only_in_a_replay(clock: VirtualClock) -> None:
    with sample_recording() as path:
        replay = make_app(ReplaySource(path, clock), clock)
        async with replay.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            text = footer_text(await footer_keys(pilot, replay, needing="Skip"))
            for label in ("Play", "Faster", "Slower", "Skip"):
                assert label in text
    live = app_with(clock)
    async with live.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        text = footer_text(await footer_keys(pilot, live, needing="Quit"))
        for label in ("Bots", "Articles", "Size", "Wiki", "Pause", "Watch", "Help", "Quit"):
            assert label in text
        for label in ("Play", "Faster", "Slower", "Skip"):
            assert label not in text


# --- the watchlist ---------------------------------------------------------------------------------------------------


@respx.mock
async def test_w_on_a_log_row_adds_that_article(clock: VirtualClock, client: Client) -> None:
    route = action_api((974, "Ada Lovelace", 500))
    app = app_with(clock, make_edit("Ada Lovelace"), client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("w")
        await finish_work(app, pilot)
        assert ("enwiki", "Ada Lovelace") in app.watchlist
        assert len(app.watchlist) == 1
        assert [n.message for n in app._notifications] == ["Watching Ada Lovelace"]
        assert route.call_count == 1
        # nothing is unseen yet, so the tab has no number
        assert str(app.query_one(TabbedContent).get_tab("watchlist").label_text) == "Watchlist"


@respx.mock
async def test_w_on_a_top_row_adds_that_article(clock: VirtualClock, client: Client) -> None:
    action_api((12, "Grace Hopper", 500))
    app = app_with(clock, make_edit("Grace Hopper"), client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        app.query_one("#top").focus()
        await pilot.pause()
        await pilot.press("w")
        await finish_work(app, pilot)
        assert ("enwiki", "Grace Hopper") in app.watchlist


@respx.mock
async def test_w_on_the_same_page_twice_says_so_and_does_not_double_count(clock: VirtualClock, client: Client) -> None:
    action_api((974, "Ada Lovelace", 500))
    app = app_with(clock, make_edit("Ada Lovelace"), client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("w")
        await finish_work(app, pilot)
        await pilot.press("w")
        await finish_work(app, pilot)
        assert len(app.watchlist) == 1
        assert [n.message for n in app._notifications] == ["Watching Ada Lovelace", "Already watching Ada Lovelace"]


@respx.mock
@pytest.mark.parametrize("tab_key", ["1", "2", "3", "4", "5"])
async def test_w_anywhere_else_opens_the_dialog_and_adds_what_you_type(
    clock: VirtualClock, client: Client, tab_key: str
) -> None:
    action_api((974, "Ada Lovelace", 500))
    respx.get(url__regex=r".*/metrics/pageviews/per-article/.*").mock(return_value=httpx.Response(404))
    app = app_with(clock, client=client)  # no edits, so the Live tab has no row to add either
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(tab_key)
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, AddPage)
        await pilot.press(*"ada lovelace", "enter")
        await finish_work(app, pilot)
        assert not isinstance(app.screen, AddPage)
        assert ("enwiki", "Ada Lovelace") in app.watchlist
        assert [n.message for n in app._notifications] == ["Watching Ada Lovelace"]


@respx.mock
async def test_the_dialog_reads_a_pasted_wikipedia_url(clock: VirtualClock, client: Client) -> None:
    route = action_api((7, "Ada Lovelace", 500))
    app = app_with(clock, client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        app.screen.query_one(Input).value = "https://de.wikipedia.org/wiki/Ada_Lovelace"
        await pilot.click("#add")
        await finish_work(app, pilot)
        assert ("dewiki", "Ada Lovelace") in app.watchlist
        assert route.calls.last.request.url.host == "de.wikipedia.org"


@respx.mock
async def test_capital_w_opens_the_dialog_even_on_a_log_row(clock: VirtualClock, client: Client) -> None:
    app = app_with(clock, make_edit("Ada Lovelace"), client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("W")
        await pilot.pause()
        assert isinstance(app.screen, AddPage)
        assert len(app.watchlist) == 0


@respx.mock
async def test_a_bad_title_or_link_is_explained_and_makes_no_request(clock: VirtualClock, client: Client) -> None:
    route = action_api()
    app = app_with(clock, client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        await pilot.press(*"Talk:Ada", "enter")
        await finish_work(app, pilot)
        assert [n.message for n in app._notifications] == [
            "Talk: pages are not articles. wikihose watches articles only."
        ]
        assert route.call_count == 0
        assert len(app.watchlist) == 0


@respx.mock
async def test_a_missing_page_is_explained(clock: VirtualClock, client: Client) -> None:
    action_api()
    app = app_with(clock, client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("2", "w")
        await pilot.pause()
        await pilot.press(*"Zzzz", "enter")
        await finish_work(app, pilot)
        assert [n.message for n in app._notifications] == ["There is no page called \u201cZzzz\u201d."]
        assert len(app.watchlist) == 0


async def test_without_a_connection_adding_says_so(clock: VirtualClock) -> None:
    app = app_with(clock, make_edit("Ada Lovelace"))  # no client, as in a replay
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("w")
        await finish_work(app, pilot)
        assert "needs a connection to Wikipedia" in next(n.message for n in app._notifications)
        assert len(app.watchlist) == 0


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


@respx.mock
async def test_a_watched_article_is_marked_in_the_log(clock: VirtualClock, client: Client) -> None:
    action_api((974, "Ada Lovelace", 500))
    app = app_with(clock, make_edit("Ada Lovelace"), make_edit("Grace Hopper"), client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        tab = await settle(app, pilot)
        app.watch_page("enwiki", "Ada Lovelace")
        await finish_work(app, pilot)
        table = app.query_one("#log", DataTable)
        by_title = {}
        for key, edit in tab._rows:
            cell = table.get_cell(key, "Article")
            by_title[edit.title] = cell
        ada, grace = by_title["Ada Lovelace"], by_title["Grace Hopper"]
        assert ada.plain.startswith("\u2605 ")  # a star, so the mark does not rely on colour
        assert all(app.current_theme.accent in str(span.style) for span in ada.spans)
        assert not grace.plain.startswith("\u2605") and not grace.spans


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


# --- every footer key works when clicked -----------------------------------------------------------------------------


async def click_footer(pilot, app, label: str) -> None:
    """Click the footer key with this label, once the footer has stopped rebuilding itself (it does so when focus moves)."""
    last, steady = None, 0
    for _ in range(100):
        keys = await footer_keys(pilot, app, needing=label)
        state = [(id(k), k.region) for k in keys]
        steady = steady + 1 if state == last else 0
        last = state
        if steady >= 3:
            break
        await pilot.pause()
    else:
        raise AssertionError(f"the footer never stopped changing while waiting to click {label!r}")
    key = next((k for k in keys if label in str(k.render())), None)
    assert key is not None, f"no footer key labelled {label!r}; the footer shows {footer_text(keys)!r}"
    await pilot.click(key)
    await pilot.pause()


@pytest.mark.parametrize("label", ["Bots", "Articles", "Size", "Wiki", "Pause", "Watch", "Help", "Quit"])
async def test_clicking_each_live_footer_key_does_what_its_key_does(clock: VirtualClock, label: str) -> None:
    app = app_with(clock)
    async with app.run_test(size=(140, 30)) as pilot:
        tab = live_tab(app)
        before = (tab.hide_bots, tab.articles_only, tab.min_bytes, tab.paused)
        await click_footer(pilot, app, label)
        after = (tab.hide_bots, tab.articles_only, tab.min_bytes, tab.paused)
        match label:
            case "Bots":
                assert after[0] != before[0]
            case "Articles":
                assert after[1] != before[1]
            case "Size":
                assert after[2] != before[2]
            case "Pause":
                assert after[3] != before[3]
            case "Wiki":
                assert isinstance(app.screen, WikiPrompt)
            case "Watch":
                assert isinstance(app.screen, AddPage)
            case "Help":
                assert isinstance(app.screen, HelpScreen)
            case "Quit":
                assert app._exit


@pytest.mark.parametrize("label", ["Play", "Faster", "Slower", "Skip"])
async def test_clicking_each_replay_footer_key_does_what_its_key_does(clock: VirtualClock, label: str) -> None:
    with sample_recording() as path:
        source = ReplaySource(path, clock)
        source.pause()
        app = make_app(source, clock)
        async with app.run_test(size=(140, 30)) as pilot:
            before = source.moment()
            await click_footer(pilot, app, label)
            match label:
                case "Play":
                    assert not source.paused
                case "Faster":
                    assert source.speed == 2
                case "Slower":
                    assert source.speed == 0.5
                case "Skip":
                    assert (source.moment() - before).total_seconds() == 60
            await wait_for_stream(app, pilot) if not source.paused else None
