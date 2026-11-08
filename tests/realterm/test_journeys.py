"""Journeys through the real program in a real pseudo-terminal, with the bytes a terminal sends.

What the Pilot tests cannot see: the SGR mouse reports (and the pointer motion before every click), the input parser,
the resize signal and the exit code of the real process. Assertions are on the rendered screen only; a failed wait
fails with the screen as it was. Run with ``pytest tests/realterm -m uat`` (see docs/development.md).
"""

from __future__ import annotations

import re
from collections.abc import Callable

import psutil
import pytest
from uat_kit import Screen, Terminal

Start = Callable[..., Terminal]
TABS = ["Live", "Reading", "Watchlist", "Diff", "Convergence"]
HELP_LINE = "Hide or show bot edits"  # a line of the help screen; Live has no such text
EDIT_ROW = re.compile(r"\d\d:\d\d:\d\d\s+[+-][\d,]+\s+\S")  # a log row: time, bytes, article

# What each tab prints, as a predicate on the screen.
SHOWN: dict[str, Callable[[Screen], bool]] = {
    "Live": lambda s: "bots hidden" in s or "bots shown" in s,
    "Reading": lambda s: "needs a live connection" in s or "Spike" in s,
    "Watchlist": lambda s: "You are not watching any pages yet" in s,
    "Diff": lambda s: "No change is open" in s,
    "Convergence": lambda s: "Live edits vs. the last full day of reading" in s,
}


def filter_line(screen: Screen) -> str:
    for line in screen.lines:
        if "bots hidden" in line or "bots shown" in line:
            return line.strip()
    raise AssertionError(f"no filter line on screen:\n{screen.text}")


def close_help(term: Terminal) -> None:
    term.send_keys("escape")
    term.wait_for(lambda s: HELP_LINE not in s, 10, "the help screen to close")
    term.settle()


def test_start_shows_the_live_tab(start: Start) -> None:
    term = start()
    screen = term.screen()
    assert screen.lines[0].lstrip().startswith("wikihose"), screen.text
    assert all(label in screen.lines[1] for label in TABS), screen.text
    term.wait_for(lambda s: bool(EDIT_ROW.search(s.text)), 20, "edits in the log")
    assert SHOWN["Live"](term.screen())


def test_number_keys_switch_tabs(start: Start) -> None:
    term = start()
    for key, label in zip("2345", TABS[1:], strict=True):  # it starts on Live, so 1 is pressed last
        term.send_keys(key)
        term.wait_for(SHOWN[label], 10, f"the {label} tab after pressing {key}")
    term.send_keys("1")
    term.wait_for(SHOWN["Live"], 10, "the Live tab again")


def test_clicking_a_tab_label_switches_tabs(start: Start) -> None:
    term = start()
    for label in ("Reading", "Watchlist", "Diff", "Convergence", "Live"):
        term.click_text(label)
        term.wait_for(SHOWN[label], 10, f"the {label} tab after clicking its label")


def test_clicking_help_in_the_footer_opens_help_and_escape_closes_it(start: Start) -> None:
    term = start()
    term.click_text("Help")
    term.wait_for(lambda s: HELP_LINE in s and "Keys" in s, 10, "the help screen after clicking Help")
    close_help(term)
    assert SHOWN["Live"](term.screen())


def test_question_mark_lists_the_keys(start: Start) -> None:
    term = start()
    term.send_keys("?")
    wanted = ("Switch tab", "Quit wikihose", "Pause or resume the replay")
    # wait for the whole list, not just its first line: the help screen can draw over more than one frame
    term.wait_for(lambda s: HELP_LINE in s and all(text in s for text in wanted), 10, "the help screen with its keys")
    close_help(term)
    term.send_keys("?")
    term.wait_for(lambda s: HELP_LINE in s, 10, "the help screen again")
    term.send_keys("?")  # the same key closes it
    term.wait_for(lambda s: HELP_LINE not in s, 10, "? to close the help screen")


def test_b_and_a_toggle_the_filter_line(start: Start) -> None:
    term = start()
    before = filter_line(term.screen())
    assert "bots hidden" in before and "articles only" in before, before
    term.send_keys("b")
    term.wait_for(lambda s: "bots shown" in filter_line(s), 10, "bots shown after b")
    term.send_keys("a")
    term.wait_for(lambda s: "all pages" in filter_line(s), 10, "all pages after a")
    term.send_keys("b", "a")
    term.wait_for(lambda s: filter_line(s) == before, 10, "the filter line back as it was")


def test_enter_over_ssh_shows_the_address_instead_of_opening_a_browser(start: Start) -> None:
    """SSH_CONNECTION makes the program treat the browser as out of reach, so no browser opens on the machine running
    this test; Enter on the first log row shows the page's address in a notice instead."""
    term = start(env={"SSH_CONNECTION": "192.0.2.1 50000 192.0.2.2 22"})
    term.wait_for(lambda s: bool(EDIT_ROW.search(s.text)), 20, "edits in the log")
    term.send_keys("enter")
    term.wait_for(
        lambda s: "No browser can be opened here" in s and "https://en.wikipedia.org/wiki/" in s,
        10,
        "the notice with the page's address after Enter",
    )


def test_replay_keys_change_the_speed_and_pause(start: Start) -> None:
    term = start()
    assert "1x" in term.screen().lines[0], term.screen_text()
    term.send_keys("+")
    term.wait_for(lambda s: "2x" in s.lines[0], 10, "2x in the top bar after +")
    term.send_keys("space")
    term.wait_for(lambda s: "paused" in s.lines[0], 10, "paused in the top bar after space")
    term.send_keys("space")
    term.wait_for(lambda s: "paused" not in s.lines[0] and "2x" in s.lines[0], 10, "playing again after space")


def test_too_small_window_says_so_and_clears(start: Start, size: tuple[int, int]) -> None:
    term = start()
    term.resize(70, 20)
    term.wait_for(lambda s: "Make the window bigger" in s, 10, "the too-small message at 70x20")
    term.resize(*size)
    term.wait_for(lambda s: "Make the window bigger" not in s and SHOWN["Live"](s), 10, "the Live tab back")


CONSOLE_HOSTS = ("conhost.exe", "openconsole.exe")  # Windows keeps these until the terminal closes


def program_processes() -> list[psutil.Process]:
    """Every process this test started, found after the app is up (a PyInstaller exe starts its real child late)."""
    found = []
    for proc in psutil.Process().children(recursive=True):
        try:
            if proc.name().lower() not in CONSOLE_HOSTS:
                found.append(proc)
        except psutil.NoSuchProcess:
            continue
    return found


def still_running(procs: list[psutil.Process]) -> list[psutil.Process]:
    _gone, alive = psutil.wait_procs(procs, timeout=5)
    running = []
    for proc in alive:
        try:
            if proc.status() != psutil.STATUS_ZOMBIE:  # exited, not yet reaped
                running.append(proc)
        except psutil.NoSuchProcess:
            continue
    return running


def test_q_exits_with_code_zero_and_the_process_is_gone(start: Start) -> None:
    term = start()
    term.wait_for(SHOWN["Live"], 10, "the Live tab")
    program = program_processes()
    assert program, "no program process found"
    term.send_keys("q")
    assert term.wait_exit(10) == 0, term.screen_text()
    running = still_running(program)
    assert not running, f"still running after q: {running}\n{term.screen_text()}"


@pytest.mark.parametrize("key", ["1", "2", "3", "4", "5"])
def test_every_tab_stays_alive_for_a_while(start: Start, key: str) -> None:
    """A tab that crashes on its first redraw would show up as an exited program, not a wrong screen."""
    term = start()
    term.send_keys(key)
    label = TABS["12345".index(key)]
    term.wait_for(SHOWN[label], 10, f"the {label} tab")
    term.hold(2)
    assert term.alive(), term.screen_text()
    assert SHOWN[label](term.screen()), term.screen_text()
