"""The bundled demo recording, ``--demo``, and the script that makes the README picture."""

from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support import footer_keys, footer_text, live_tab, make_app, wait_for_stream

from wikihose.cli import main
from wikihose.clock import VirtualClock
from wikihose.headless import run_headless
from wikihose.sources.recording import demo_recording, read_recording, sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.ui.tabs.live import FilterLine, LiveTab

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo_path() -> Any:
    with demo_recording() as path:
        yield path


def load_capture_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_demo", ROOT / "scripts" / "capture_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capture_demo"] = module
    spec.loader.exec_module(module)
    return module


# --- the recording -----------------------------------------------------------------------------------------------


def test_the_demo_has_no_email_or_key_like_strings(demo_path: Path) -> None:
    with gzip.open(demo_path, "rt", encoding="utf-8") as handle:
        text = handle.read()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "email-like string"
    # Looser than the sample's check on purpose: article titles such as "Secretary of State" contain "secret".
    assert not re.search(r"(?i)api[_-]?key|\bsecret[_-]?(key|token)|passw(or)?d\s*[:=]|bearer [\w.-]{10,}", text)
    assert "BEGIN PRIVATE KEY" not in text and "BEGIN RSA PRIVATE" not in text
    assert not re.search(r"[A-Fa-f0-9]{32,}", text), "hex key-like string"
    assert not re.search(r"(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{32,}", text)
    assert not re.search(r"ghp_|github_pat_|sk-[A-Za-z0-9]|AKIA[A-Z0-9]{8}|xox[bp]-", text), "token prefix"


def test_the_demo_holds_only_enwiki_events_and_parses_cleanly(demo_path: Path) -> None:
    header, events = read_recording(demo_path)
    assert header.wiki == "enwiki"
    wikis = {json.loads(event.data).get("wiki") for event in events}
    assert wikis == {"enwiki"}
    assert events.skipped / 1822 < 0.01


def test_the_demo_is_six_minutes_and_small(demo_path: Path) -> None:
    _, events = read_recording(demo_path)
    times = [event.t for event in events]
    assert len(times) > 1500
    assert 355 < max(times) < 365
    assert demo_path.stat().st_size < 300_000


def test_the_demo_is_a_different_file_from_the_sample() -> None:
    with demo_recording() as demo, sample_recording() as sample:
        assert demo != sample


async def test_the_demo_plays_through_replay_without_waiting(clock: VirtualClock, demo_path: Path) -> None:
    summary = await run_headless(ReplaySource(demo_path, clock), "enwiki", 1000)
    edits = int(re.match(r"edits=(\d+)", summary).group(1))  # type: ignore[union-attr]
    assert edits > 1000
    assert clock.monotonic() > 300  # virtual time passed, no real time


# --- --demo ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("other", [["--replay", "x.gz"], ["--record", "x.gz"]])
def test_demo_cannot_be_combined_with_replay_or_record(other: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--demo", *other])
    assert exit_info.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


@pytest.mark.parametrize("other", [["--wiki", "dewiki"], ["--speed", "2"]])
def test_demo_refuses_a_wiki_or_a_speed(other: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--demo", *other])
    assert exit_info.value.code == 2


def test_demo_headless_runs_with_no_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--demo must not touch the network")

    monkeypatch.setattr("wikihose.net.Client", refuse)
    monkeypatch.setattr("wikihose.sources.live.LiveSource", refuse)
    assert main(["--demo", "--headless-seconds", "0"]) == 0
    assert capsys.readouterr().out.startswith("edits=")


def test_demo_forces_enwiki_keeps_the_theme_and_never_writes_the_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wikihose.paths import config_dir

    config_dir().mkdir(parents=True)
    path = config_dir() / "config.toml"
    path.write_text('theme = "wikihose-light"\nwiki = "dewiki"\n', encoding="utf-8")
    before = path.read_bytes()
    seen: dict[str, Any] = {}

    class FakeApp:
        def __init__(self, source: object, config: Any, **kwargs: Any) -> None:
            seen.update(source=source, config=config, **kwargs)

        async def run_async(self) -> None:
            seen["ran"] = True

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("--demo must not touch the network")

    monkeypatch.setattr("wikihose.ui.app.WikihoseApp", FakeApp)
    monkeypatch.setattr("wikihose.net.Client", refuse)
    monkeypatch.setattr("wikihose.sources.live.LiveSource", refuse)
    assert main(["--demo"]) == 0
    assert seen["ran"]
    assert isinstance(seen["source"], ReplaySource)
    assert seen["config"].wiki == "enwiki"
    assert seen["config"].theme == "wikihose-light"
    assert seen["config_path"] is None
    assert seen["store"] is None
    assert seen["loop_replay"] is True
    assert path.read_bytes() == before


def test_replay_does_not_loop(monkeypatch: pytest.MonkeyPatch, sample_path: Path) -> None:
    seen: dict[str, Any] = {}

    class FakeApp:
        def __init__(self, source: object, config: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

        async def run_async(self) -> None:
            pass

    monkeypatch.setattr("wikihose.ui.app.WikihoseApp", FakeApp)
    assert main(["--replay", str(sample_path)]) == 0
    assert seen["loop_replay"] is False


async def test_a_looping_replay_starts_over_with_an_empty_log(clock: VirtualClock, sample_path: Path) -> None:
    source = ReplaySource(sample_path, clock)
    rewinds: list[int] = []
    rewind = source.rewind

    def rewind_and_hold() -> bool:
        ok = rewind()
        source.pause()  # hold the second pass at its start so the empty log can be looked at
        rewinds.append(live_tab(app).stats.total)
        return ok

    source.rewind = rewind_and_hold  # type: ignore[method-assign]
    app = make_app(source, clock, loop_replay=True)
    async with app.run_test(size=(100, 30)) as pilot:
        for _ in range(1000):
            if rewinds:
                break
            await pilot.pause()
        assert rewinds and rewinds[0] > 0  # the first pass had counted edits
        await pilot.pause()
        tab = live_tab(app)
        assert tab.stats.total == 0
        assert tab.row_titles() == []
        assert not app.replay_finished
        assert str(app.query_one("#topbar-right").render()) == "Recorded data, not live  paused"
        assert source.moment() == source.recorded_start


async def test_the_loop_survives_a_dialog_being_open(clock: VirtualClock, sample_path: Path) -> None:
    from wikihose.ui.dialogs import HelpScreen

    source = ReplaySource(sample_path, clock)
    rewound: list[bool] = []
    rewind = source.rewind

    def rewind_and_hold() -> bool:
        ok = rewind()
        source.pause()
        rewound.append(ok)
        return ok

    source.rewind = rewind_and_hold  # type: ignore[method-assign]
    app = make_app(source, clock, loop_replay=True)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        for _ in range(1000):
            if rewound:
                break
            await pilot.pause()
        await pilot.pause()
        assert rewound == [True]
        assert app.is_running
        assert isinstance(app.screen, HelpScreen)
        assert app._main.query_one(LiveTab).stats.total == 0


async def test_a_closed_replay_cannot_be_rewound(
    clock: VirtualClock, sample_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = ReplaySource(sample_path, clock)
    await source.aclose()
    opened: list[object] = []
    monkeypatch.setattr("wikihose.sources.replay.read_recording", lambda path: opened.append(path))
    assert source.rewind() is False
    assert opened == []
    assert [item async for item in source.items()][-1].state == "down"  # type: ignore[union-attr]


async def test_the_demo_does_not_offer_or_allow_a_wiki_change(clock: VirtualClock, sample_path: Path) -> None:
    source = ReplaySource(sample_path, clock)
    source.pause()
    app = make_app(source, clock, loop_replay=True)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        footer = footer_text(await footer_keys(pilot, app, needing="Bots"))
        assert "Wiki" not in footer
        assert "Bots" in footer
        await pilot.press("i")
        await pilot.pause()
        assert type(app.screen).__name__ == "MainScreen"
        await app.query_one(FilterLine).run_action("pick('wiki')")
        await pilot.pause()
        assert type(app.screen).__name__ == "MainScreen"


def test_a_demo_that_cannot_be_read_says_so_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from contextlib import contextmanager

    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"nope")

    @contextmanager
    def broken() -> Any:
        yield bad

    monkeypatch.setattr("wikihose.sources.recording.demo_recording", broken)
    assert main(["--demo", "--headless-seconds", "0"]) == 1
    assert "wikihose:" in capsys.readouterr().err


async def test_a_replay_without_the_loop_stays_finished(clock: VirtualClock, sample_path: Path) -> None:
    source = ReplaySource(sample_path, clock)
    app = make_app(source, clock)
    async with app.run_test(size=(100, 30)) as pilot:
        await wait_for_stream(app, pilot)
        await pilot.pause()
        assert str(app.query_one("#topbar-right").render()) == "Recorded data, not live  replay finished"


async def test_rewind_plays_the_recording_again(clock: VirtualClock, sample_path: Path) -> None:
    source = ReplaySource(sample_path, clock)
    first = [item async for item in source.items()]
    source.rewind()
    second = [item async for item in source.items()]
    assert len(first) == len(second) > 2
    assert source.moment() > source.recorded_start


# --- the README picture ------------------------------------------------------------------------------------------


def test_the_capture_script_refuses_when_no_color_is_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = load_capture_script()
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(script, "OUTPUT", Path("never-written.svg"))
    assert script.main() == 1
    assert "NO_COLOR" in capsys.readouterr().err
    assert not Path("never-written.svg").exists()


def test_the_capture_is_deterministic_and_matches_the_committed_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.delenv("NO_COLOR", raising=False)  # the picture is made in colour
    script = load_capture_script()
    first = asyncio.run(script.capture())
    second = asyncio.run(script.capture())
    assert first == second
    committed = (ROOT / "docs" / "images" / "demo.svg").read_text(encoding="utf-8")
    assert first == committed, "docs/images/demo.svg is out of date; run scripts/capture_demo.py and read the result"
    assert "recorded&#160;data,&#160;not&#160;live" in first  # the window title
