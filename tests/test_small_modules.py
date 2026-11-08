"""Clock, paths, config, titles, store and the no-sleep rule."""

from __future__ import annotations

import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wikihose import paths
from wikihose.clock import SystemClock, VirtualClock
from wikihose.config import Config, load_config, save_config
from wikihose.store import SCHEMA_VERSION, Store
from wikihose.titles import normalize_title, page_key, title_from_url

SRC = Path(__file__).resolve().parent.parent / "src"

# --- clock -----------------------------------------------------------------------------------------------------------


async def test_virtual_sleep_advances_time_without_waiting(clock: VirtualClock) -> None:
    before = clock.now()
    await clock.sleep(3600)
    assert clock.monotonic() == 3600
    assert (clock.now() - before).total_seconds() == 3600


def test_virtual_clock_rejects_naive_start_and_negative_advance() -> None:
    with pytest.raises(ValueError):
        VirtualClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        VirtualClock(datetime(2026, 1, 1, tzinfo=UTC)).advance(-1)


async def test_system_clock_is_aware_and_sleeps() -> None:
    clock = SystemClock()
    assert clock.now().tzinfo is not None
    start = clock.monotonic()
    await clock.sleep(0)
    assert clock.monotonic() >= start


def test_nothing_outside_clock_py_sleeps() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "clock.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "time.sleep(" in text or "asyncio.sleep(" in text:
            offenders.append(path.name)
    assert offenders == []


# --- paths -----------------------------------------------------------------------------------------------------------


def test_path_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIHOSE_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("WIKIHOSE_DATA_DIR", str(tmp_path / "d"))
    assert paths.config_dir() == tmp_path / "c"
    assert paths.data_dir() == tmp_path / "d"
    assert paths.log_dir() == tmp_path / "d" / "logs"
    assert paths.cache_dir() == tmp_path / "d" / "http-cache"
    assert paths.db_path() == tmp_path / "d" / "wikihose.db"
    assert not (tmp_path / "d").exists()  # nothing is created by asking


def test_paths_without_overrides_end_in_the_app_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WIKIHOSE_CONFIG_DIR")
    monkeypatch.delenv("WIKIHOSE_DATA_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    if sys.platform == "win32":
        assert paths.config_dir() == paths.data_dir() == tmp_path / "wikihose"
        assert paths.log_dir() == tmp_path / "wikihose" / "Logs"
    else:
        assert "wikihose" in paths.config_dir().parts
        assert "wikihose" in paths.data_dir().parts


# --- config ----------------------------------------------------------------------------------------------------------


def test_config_defaults_for_missing_file_and_no_config(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.toml") == (Config(), [])
    (tmp_path / "config.toml").write_text('wiki = "dewiki"\n', encoding="utf-8")
    assert load_config(None) == (Config(), [])  # --no-config ignores an existing file


def test_config_drops_unknown_keys_and_bad_values_with_a_warning_each(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'wiki = "Not A Wiki"\nmin_bytes = -3\nhide_bots = "yes"\ncolour = "red"\ntheme = "wikihose-light"\n',
        encoding="utf-8",
    )
    config, warnings = load_config(path)
    assert config == Config(theme="wikihose-light")
    assert len(warnings) == 4
    assert any("colour" in w for w in warnings)
    assert any("wiki" in w for w in warnings)


def test_config_unreadable_toml_gives_defaults_and_a_warning(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is = not [toml", encoding="utf-8")
    config, warnings = load_config(path)
    assert config == Config()
    assert len(warnings) == 1


def test_config_round_trip_in_the_env_dir() -> None:
    path = paths.config_dir() / "config.toml"
    config = Config(wiki="frwiki", contact="https://example.org/me", hide_bots=False, min_bytes=20)
    save_config(config, path)
    assert load_config(path) == (config, [])
    assert not list(path.parent.glob("*.tmp"))


# --- titles ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Ada_Lovelace", "Ada Lovelace"),
        ("  ada   lovelace ", "Ada lovelace"),
        ("Avengers:_Doomsday", "Avengers: Doomsday"),
        ("a\tb\nc", "A b c"),
        ("", ""),
        ("élan", "Élan"),
    ],
)
def test_normalize_title(given: str, expected: str) -> None:
    assert normalize_title(given) == expected


def test_normalize_title_keeps_case_when_asked() -> None:
    assert normalize_title("run_up", first_letter_upper=False) == "run up"


def test_page_key_is_case_sensitive_for_wiktionary() -> None:
    assert page_key("enwiki", "ada_lovelace") == ("enwiki", "Ada lovelace")
    assert page_key("enwiktionary", "run_up") == ("enwiktionary", "run up")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://en.wikipedia.org/wiki/Ada_Lovelace", ("enwiki", "Ada Lovelace")),
        ("https://en.wikipedia.org/wiki/Avengers:_Doomsday", ("enwiki", "Avengers: Doomsday")),
        ("https://en.m.wikipedia.org/wiki/Ada_Lovelace", ("enwiki", "Ada Lovelace")),
        ("https://fr.wikipedia.org/wiki/%C3%89lan_vital", ("frwiki", "Élan vital")),
        ("https://en.wikipedia.org/wiki/AC%2FDC", ("enwiki", "AC/DC")),
        ("https://en.wikipedia.org/w/index.php?title=Ada_Lovelace&action=history", ("enwiki", "Ada Lovelace")),
        ("https://be-x-old.wikipedia.org/wiki/Test", ("be_x_oldwiki", "Test")),
        ("  https://en.wikipedia.org/wiki/Ada_Lovelace  ", ("enwiki", "Ada Lovelace")),
        ("https://en.wiktionary.org/wiki/run_up", ("enwiktionary", "run up")),
    ],
)
def test_title_from_url(url: str, expected: tuple[str, str]) -> None:
    assert title_from_url(url) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Ada Lovelace",
        "https://example.com/wiki/Ada_Lovelace",
        "https://en.wikipedia.org/",
        "https://en.wikipedia.org/wiki/",
        "https://en.wikipedia.org/w/index.php?action=history",
        "ftp://en.wikipedia.org/wiki/Ada",
        "https://evil.org/en.wikipedia.org/wiki/Ada",
        "http://[bad",
        "",
    ],
)
def test_title_from_url_rejects_other_text(text: str) -> None:
    assert title_from_url(text) is None


# --- store -----------------------------------------------------------------------------------------------------------


def test_store_migrates_an_empty_database_to_v1() -> None:
    store = Store(":memory:")
    assert store.schema_version() == SCHEMA_VERSION == 1
    store.close()


def test_store_reopen_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "wikihose.db"
    first = Store(path)
    first.add_edit_minute("enwiki", "Ada Lovelace", "2026-11-07T12:00", human_edits=1)
    first.close()
    second = Store(path)
    assert second.schema_version() == 1
    assert second.edit_minute("enwiki", "Ada Lovelace", "2026-11-07T12:00") == (1, 1, 0, 0)
    second.close()


def test_store_has_the_v1_tables() -> None:
    store = Store(":memory:")
    tables = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"pageviews", "baselines", "watchlist", "last_seen", "edit_minutes"}
    store.close()


def test_store_upserts_edit_minutes_and_prunes() -> None:
    store = Store(":memory:")
    store.add_edit_minute("enwiki", "A", "2026-11-07T12:00", bytes_added=10)
    store.add_edit_minute("enwiki", "A", "2026-11-07T12:00", human_edits=1, bytes_added=5, bytes_removed=2)
    store.add_edit_minute("enwiki", "A", "2026-11-07T12:05")
    assert store.edit_minute("enwiki", "A", "2026-11-07T12:00") == (2, 1, 15, 2)
    assert store.edit_minute("enwiki", "B", "2026-11-07T12:00") is None
    assert store.prune_edit_minutes("2026-11-07T12:01") == 1
    assert store.edit_minute("enwiki", "A", "2026-11-07T12:00") is None
    assert store.edit_minute("enwiki", "A", "2026-11-07T12:05") is not None
    store.close()


def test_store_refuses_a_newer_database(tmp_path: Path) -> None:
    path = tmp_path / "new.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(RuntimeError):
        Store(path)


def test_the_sleep_rule_has_something_to_find() -> None:
    assert re.search(r"asyncio\.sleep\(", (SRC / "wikihose" / "clock.py").read_text(encoding="utf-8"))
