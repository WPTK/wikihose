"""The Reading numbers recorded with the demo: the bundled file, the source that serves it and the demo app using it."""

from __future__ import annotations

import asyncio
import asyncio.base_events
import asyncio.proactor_events
import asyncio.selector_events
import gzip
import importlib.resources
import re
import socket
from typing import Any

import pytest
from support import ListSource, make_app, make_edit, wait_for_stream
from test_convergence import open_tab, table_rows, text_of
from test_reading_tab import settle_reading, table

from wikihose.cli import demo_reading_factory, main
from wikihose.clock import VirtualClock
from wikihose.demo_reading import DEMO_READING, FORMAT, VERSION, DemoReading, read_demo_reading
from wikihose.reading import MIN_VIEWS
from wikihose.sources.recording import demo_recording
from wikihose.sources.replay import ReplaySource


def snapshot_text() -> str:
    resource = importlib.resources.files("wikihose") / "data" / DEMO_READING
    with resource.open("rb") as raw, gzip.open(raw, "rt", encoding="utf-8") as handle:
        return handle.read()


async def test_the_snapshot_is_a_full_ranking() -> None:
    source = read_demo_reading()
    rows = await source.load()
    assert source.day is not None
    assert len(rows) >= 25
    assert rows == sorted(rows, key=lambda r: (-r.multiple, r.title))
    assert all(r.views >= MIN_VIEWS for r in rows)
    assert sum(r.exact for r in rows) >= 25  # the top candidates have their own history
    assert await source.summary(rows[0].title) is not None
    assert await source.spike_of(rows[0].title) is None  # a replay never fetches


def test_the_snapshot_has_no_email_or_key_like_strings() -> None:
    text = snapshot_text()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "email-like string"
    assert not re.search(r"(?i)api[_-]?key|\bsecret[_-]?(key|token)|passw(or)?d\s*[:=]|bearer [\w.-]{10,}", text)
    assert not re.search(r"[A-Fa-f0-9]{32,}", text), "hex key-like string"
    assert not re.search(r"ghp_|github_pat_|sk-[A-Za-z0-9]|AKIA[A-Z0-9]{8}|xox[bp]-", text), "token prefix"


def test_the_snapshot_is_small() -> None:
    resource = importlib.resources.files("wikihose") / "data" / DEMO_READING
    assert len(resource.read_bytes()) < 100_000


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"day": "not a day", "rows": [{"title": "A", "views": 9, "baseline": 1, "multiple": 9, "exact": True}]},
        {"day": "2026-11-07", "rows": "nope", "summaries": []},
    ],
)
async def test_a_damaged_snapshot_gives_an_empty_ranking(payload: Any) -> None:
    assert await DemoReading(payload).load() == []


async def test_bad_rows_and_summaries_are_skipped() -> None:
    good = {"title": "Good", "views": 9000, "baseline": 10.0, "multiple": 900.0, "series": [1, 2], "exact": True}
    rows: list[Any] = [good, {"title": "", "views": 1}, {**good, "views": -1}, {**good, "series": ["x"]}, 7]
    rows += [{**good, "views": True}, {**good, "series": [True]}, {**good, "multiple": float("nan")}]
    rows += [{**good, "baseline": float("inf")}]
    payload = {"format": FORMAT, "version": VERSION, "day": "2026-11-07", "rows": rows}
    payload["summaries"] = {"Good": {"title": "Good", "description": "", "extract": "Text."}, "Bad": {"title": 1}}
    source = DemoReading(payload)
    assert [r.title for r in await source.load()] == ["Good"]
    assert await source.summary("Good") is not None and await source.summary("Bad") is None


def test_the_demo_factory_serves_enwiki_only() -> None:
    factory = demo_reading_factory()
    assert isinstance(factory("enwiki"), DemoReading)
    assert factory("dewiki") is None


async def test_the_demo_fills_the_reading_tab(clock: VirtualClock) -> None:
    app = make_app(ListSource(clock), clock, reading=demo_reading_factory())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("2")
        await settle_reading(app, pilot)
        assert table(app).row_count == 25
        assert "needs a live connection" not in str(app.query_one("#reading-status").render())


async def test_the_demo_fills_the_convergence_tab(clock: VirtualClock) -> None:
    rows = await read_demo_reading().load()
    hot = next(r for r in rows if r.multiple >= 2)
    app = make_app(ListSource(clock, [make_edit(hot.display_title)]), clock, reading=demo_reading_factory())
    async with app.run_test(size=(80, 24)) as pilot:
        await wait_for_stream(app, pilot)
        await open_tab(app, pilot)
        assert table_rows(app)[0][0] == hot.display_title
        assert "(UTC)" in text_of(app, "convergence-heading")


# --- no network, checked at the socket -------------------------------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every name lookup and every TCP connect to anything but loopback fails and is recorded.

    Blocked in the socket module (lookups, ``connect``, ``connect_ex``) and in asyncio itself (``create_connection``
    and ``sock_connect``), because on Windows the proactor loop connects through ``ConnectEx`` without calling
    ``socket.connect``, and skips the lookup for an IP address. Loopback stays open for the event loop's own socket pair.
    """
    attempts: list[Any] = []
    real_connect = socket.socket.connect

    def loopback(host: Any) -> bool:
        return host in ("127.0.0.1", "::1")

    def connect(self: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and loopback(address[0]):
            return real_connect(self, address)
        attempts.append(address)
        raise OSError("network blocked by the test")

    def lookup(*args: Any, **kwargs: Any) -> Any:
        attempts.append(args[:2])
        raise socket.gaierror("network blocked by the test")

    async def create_connection(self: Any, factory: Any, host: Any = None, port: Any = None, **kwargs: Any) -> Any:
        attempts.append((host, port))
        raise OSError("network blocked by the test")

    def guard_sock_connect(real: Any) -> Any:
        async def sock_connect(self: Any, sock: socket.socket, address: Any) -> Any:
            if isinstance(address, tuple) and loopback(address[0]):
                return await real(self, sock, address)
            attempts.append(address)
            raise OSError("network blocked by the test")

        return sock_connect

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: connect(self, address))
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection", create_connection)
    for loop_class in (
        asyncio.base_events.BaseEventLoop,
        asyncio.selector_events.BaseSelectorEventLoop,
        asyncio.proactor_events.BaseProactorEventLoop,
    ):
        monkeypatch.setattr(loop_class, "sock_connect", guard_sock_connect(loop_class.sock_connect))
    return attempts


def test_the_guard_catches_an_ip_address_too(no_network: list[Any]) -> None:
    async def reach() -> None:
        await asyncio.get_running_loop().create_connection(asyncio.Protocol, "192.0.2.1", 443)

    with pytest.raises(OSError, match="blocked"):
        asyncio.run(reach())
    assert no_network == [("192.0.2.1", 443)]


def test_the_demo_wiring_gives_the_app_no_client_and_the_recorded_reading(
    no_network: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    built: dict[str, Any] = {}

    class FakeApp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.update(kwargs)

        async def run_async(self) -> None:
            return None

    import wikihose.ui.app

    monkeypatch.setattr(wikihose.ui.app, "WikihoseApp", FakeApp)
    assert main(["--demo", "--no-config"]) == 0
    assert built["client"] is None
    assert isinstance(built["reading"]("enwiki"), DemoReading)
    assert no_network == []


def test_demo_headless_opens_no_socket(no_network: list[Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--demo", "--no-config", "--headless-seconds", "30"]) == 0
    assert "edits=" in capsys.readouterr().out
    assert no_network == []


async def test_the_demo_app_opens_no_socket_on_any_tab(no_network: list[Any], clock: VirtualClock) -> None:
    with demo_recording() as path:
        app = make_app(ReplaySource(path, clock), clock, reading=demo_reading_factory(), loop_replay=False)
        async with app.run_test(size=(80, 24)) as pilot:
            for key in "12345":
                await pilot.press(key)
                await pilot.pause()
            await settle_reading(app, pilot)
            await app.workers.wait_for_complete()
            await pilot.press("w")  # watching needs a connection: the demo must say so, not try
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert no_network == []
