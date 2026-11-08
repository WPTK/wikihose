"""``wikihose --selftest``: checks the install without the network, in a few seconds.

Prints one line per check, then ``selftest: ok`` or ``selftest: FAILED (n)``. The checks use the sample recording that
ships in the wheel, so a broken package or a missing data file shows up here.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from wikihose.clock import VirtualClock
from wikihose.config import Config, load_config, save_config
from wikihose.net import RequestBudget
from wikihose.sources.events import Edit, FeedStatus, PageMove, parse_recentchange
from wikihose.sources.recording import read_recording, sample_recording
from wikihose.sources.replay import ReplaySource
from wikihose.store import SCHEMA_V1, SCHEMA_VERSION, Store
from wikihose.ui.theme import THEMES, orange_roles


def _themes() -> None:
    names = [theme.name for theme in THEMES]
    assert len(set(names)) == len(names) >= 2, f"themes: {names}"
    assert Config().theme in names, "the default theme is not registered"
    for theme in THEMES:
        assert not orange_roles(theme), f"{theme.name} has orange {orange_roles(theme)}"


def _config() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "config.toml"
        assert load_config(path) == (Config(), []), "defaults did not load"
        save_config(Config(wiki="dewiki"), path)
        assert load_config(path) == (Config(wiki="dewiki"), []), "saved config did not round-trip"


def _store() -> None:
    store = Store(":memory:")
    try:
        assert store.schema_version() == SCHEMA_VERSION == 2, "the database did not migrate to version 2"
    finally:
        store.close()
    with tempfile.TemporaryDirectory() as folder:  # a version 1 database with a watched page moves up to version 2
        path = Path(folder) / "v1.db"
        old = sqlite3.connect(path)
        for statement in SCHEMA_V1:
            old.execute(statement)
        old.execute("INSERT INTO watchlist VALUES ('enwiki', 974, 'Ada Lovelace', '2026-11-07T12:00:00+00:00')")
        old.execute("INSERT INTO last_seen VALUES ('enwiki', 974, 1384127064, '2026-11-07T12:00:00+00:00')")
        old.execute("PRAGMA user_version = 1")
        old.commit()
        old.close()
        upgraded = Store(path)
        try:
            (page,) = upgraded.watched_pages()
            assert (page.title, page.seen_rev_id, page.last_rev_id, page.unseen) == (
                "Ada Lovelace",
                1384127064,
                1384127064,
                0,
            )
        finally:
            upgraded.close()


def _sample_counts() -> tuple[int, int]:
    """(edits and new pages, moves) among the sample's events, by parsing them directly."""
    with sample_recording() as path:
        _, events = read_recording(path)
        parsed = [parse_recentchange(event.data) for event in events]
    return (
        sum(isinstance(item, Edit) for item in parsed),
        sum(isinstance(item, PageMove) for item in parsed),
    )


def _parse() -> None:
    edits, moves = _sample_counts()
    assert edits > 0 and moves > 0, f"the sample parsed to {edits} edits and {moves} moves"


def _replay() -> None:
    edits, moves = _sample_counts()

    async def play() -> tuple[list[Edit | PageMove], list[FeedStatus], float]:
        clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
        with sample_recording() as path:
            source = ReplaySource(path, clock, speed=16.0)
            items = [item async for item in source.items()]
        return (
            [item for item in items if not isinstance(item, FeedStatus)],
            [item for item in items if isinstance(item, FeedStatus)],
            clock.monotonic(),
        )

    played, statuses, waited = asyncio.run(play())
    assert len(played) == edits + moves, f"replay yielded {len(played)} items, expected {edits + moves}"
    assert [status.state for status in statuses] == ["ok", "down"], "replay did not start with ok and end with down"
    assert waited > 0, "replay did not pace itself"


def _budget() -> None:
    async def run() -> float:
        clock = VirtualClock(datetime(2026, 1, 1, tzinfo=UTC))
        budget = RequestBudget(clock)
        for _ in range(150):
            async with budget.slot():
                pass
        assert clock.monotonic() == 0.0, "the first 150 requests had to wait"
        async with budget.slot():
            pass
        return clock.monotonic()

    waited = asyncio.run(run())
    assert waited >= 60.0, f"the 151st request waited {waited}s, expected the window to roll"


CHECKS: list[tuple[str, Callable[[], None]]] = [
    ("themes are registered and not orange", _themes),
    ("config defaults load and round-trip", _config),
    ("database migrates to version 2, from version 1 too", _store),
    ("sample events parse", _parse),
    ("replay of the sample", _replay),
    ("request budget admits 150, then waits", _budget),
]


def run_selftest() -> int:
    failures = 0
    for label, check in CHECKS:
        try:
            check()
        except Exception as exc:  # a failed check must not stop the others
            failures += 1
            print(f"FAIL  {label}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {label}")
    if failures:
        print(f"selftest: FAILED ({failures})")
        return 1
    print("selftest: ok")
    return 0
