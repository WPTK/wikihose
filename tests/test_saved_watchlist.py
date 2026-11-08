"""Reading the saved watchlist for a replay: read-only, and never a crash."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from wikihose.store import SCHEMA_V1, SCHEMA_VERSION, Store, read_saved_watchlist

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def test_a_missing_file_gives_nothing_and_is_not_created(tmp_path: Path) -> None:
    assert read_saved_watchlist(tmp_path / "wikihose.db") == []
    assert list(tmp_path.iterdir()) == []


def test_a_saved_list_is_read_and_the_file_is_not_touched(tmp_path: Path) -> None:
    path = tmp_path / "wikihose.db"
    store = Store(path)
    store.add_watch("enwiki", 7, "Ada Lovelace", NOW, 100)
    store.close()
    before = snapshot(tmp_path)
    pages = read_saved_watchlist(path)
    assert [(p.wiki, p.page_id, p.title, p.seen_rev_id) for p in pages] == [("enwiki", 7, "Ada Lovelace", 100)]
    assert snapshot(tmp_path) == before


def test_a_corrupt_file_gives_nothing(tmp_path: Path) -> None:
    path = tmp_path / "wikihose.db"
    path.write_bytes(b"this is not a database" * 100)
    before = snapshot(tmp_path)
    assert read_saved_watchlist(path) == []
    assert snapshot(tmp_path) == before


def test_a_file_from_a_newer_wikihose_gives_nothing_and_is_not_touched(tmp_path: Path) -> None:
    path = tmp_path / "wikihose.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE something_new (x)")
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    db.commit()
    db.close()
    before = snapshot(tmp_path)
    assert read_saved_watchlist(path) == []
    assert snapshot(tmp_path) == before


def test_a_version_one_file_is_not_migrated(tmp_path: Path) -> None:
    path = tmp_path / "wikihose.db"
    db = sqlite3.connect(path)
    for statement in SCHEMA_V1:
        db.execute(statement)
    db.execute("INSERT INTO watchlist VALUES ('enwiki', 7, 'Ada Lovelace', '2026-11-07T12:00:00+00:00')")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    before = snapshot(tmp_path)
    assert read_saved_watchlist(path) == []  # the v1 table has no unseen counts to read
    assert snapshot(tmp_path) == before
