"""Store migration 2 and the watchlist methods."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from wikihose.store import MIGRATIONS, SCHEMA_V1, SCHEMA_V2, SCHEMA_VERSION, Store

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 7, 13, 0, tzinfo=UTC)


def make_v1_database(path: Path) -> None:
    """A database as version 1 of wikihose left it, with a watched page, its last-seen row and other data."""
    db = sqlite3.connect(path)
    for statement in SCHEMA_V1:
        db.execute(statement)
    db.execute("INSERT INTO watchlist VALUES ('enwiki', 974, 'Ada Lovelace', '2026-11-01T10:00:00+00:00')")
    db.execute("INSERT INTO watchlist VALUES ('dewiki', 5, 'Seite', '2026-11-02T10:00:00+00:00')")  # never seen
    db.execute("INSERT INTO last_seen VALUES ('enwiki', 974, 1383337217, '2026-11-03T10:00:00+00:00')")
    db.execute("INSERT INTO edit_minutes VALUES ('enwiki', 'Ada Lovelace', '2026-11-07T12:00', 3, 2, 10, 4)")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()


def test_migration_2_is_the_second_step() -> None:
    assert SCHEMA_VERSION == len(MIGRATIONS) == 2 and MIGRATIONS[0] is SCHEMA_V1 and MIGRATIONS[1] is SCHEMA_V2


def test_a_version_1_database_with_data_migrates_and_keeps_everything(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    make_v1_database(path)

    store = Store(path)

    assert store.schema_version() == 2
    ada, seite = store.watched_pages()
    assert (ada.wiki, ada.page_id, ada.title, ada.added_at) == (
        "enwiki",
        974,
        "Ada Lovelace",
        datetime(2026, 11, 1, 10, tzinfo=UTC),
    )
    assert (ada.unseen, ada.seen_rev_id, ada.last_rev_id, ada.last_edit_at, ada.deleted) == (
        0,
        1383337217,
        1383337217,
        None,
        False,
    )
    assert (seite.seen_rev_id, seite.last_rev_id, seite.unseen) == (0, 0, 0)
    assert store.edit_minute("enwiki", "Ada Lovelace", "2026-11-07T12:00") == (3, 2, 10, 4)
    store.close()

    again = Store(path)  # opening a migrated database changes nothing
    assert again.schema_version() == 2 and len(again.watched_pages()) == 2
    again.close()


def test_the_new_columns_exist_with_defaults(tmp_path: Path) -> None:
    store = Store(tmp_path / "new.db")
    columns = {row[1]: row for row in store._db.execute("PRAGMA table_info(watchlist)")}
    assert set(columns) == {"wiki", "page_id", "title", "added_at", "unseen", "last_rev_id", "last_edit_at", "deleted"}
    store.close()


def test_add_watch_starts_seen_and_ignores_a_second_add() -> None:
    store = Store(":memory:")
    assert store.add_watch("enwiki", 974, "Ada Lovelace", T0, 100)
    assert not store.add_watch("enwiki", 974, "Ada Lovelace", T1, 200)
    (page,) = store.watched_pages()
    assert (page.seen_rev_id, page.last_rev_id, page.unseen, page.added_at) == (100, 100, 0, T0)
    assert store.watched_page("enwiki", 974) == page and store.watched_page("enwiki", 1) is None
    store.close()


def test_the_same_page_id_on_two_wikis_is_two_pages() -> None:
    store = Store(":memory:")
    store.add_watch("enwiki", 5, "A", T0, 1)
    store.add_watch("dewiki", 5, "B", T1, 2)
    assert [(p.wiki, p.title) for p in store.watched_pages()] == [("enwiki", "A"), ("dewiki", "B")]
    store.close()


def test_state_rename_and_mark_seen_round_trip() -> None:
    store = Store(":memory:")
    store.add_watch("enwiki", 974, "Ada Lovelace", T0, 100)
    store.set_watch_state("enwiki", 974, unseen=3, last_rev_id=130, last_edit_at=T1, deleted=True)
    store.rename_watch("enwiki", 974, "Ada, Countess of Lovelace")
    page = store.watched_page("enwiki", 974)
    assert page is not None
    assert (page.title, page.unseen, page.last_rev_id, page.last_edit_at, page.deleted, page.seen_rev_id) == (
        "Ada, Countess of Lovelace",
        3,
        130,
        T1,
        True,
        100,
    )
    store.mark_watch_seen("enwiki", 974, T1)
    page = store.watched_page("enwiki", 974)
    assert page is not None and (page.unseen, page.seen_rev_id, page.last_rev_id) == (0, 130, 130)
    assert store._db.execute("SELECT seen_at FROM last_seen").fetchone()[0] == T1.isoformat()
    store.close()


def test_remove_watch_deletes_the_last_seen_row_too() -> None:
    store = Store(":memory:")
    store.add_watch("enwiki", 974, "Ada Lovelace", T0, 100)
    assert store.remove_watch("enwiki", 974)
    assert not store.remove_watch("enwiki", 974)
    assert store.watched_pages() == []
    assert store._db.execute("SELECT count(*) FROM last_seen").fetchone()[0] == 0
    store.close()
