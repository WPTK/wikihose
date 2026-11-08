"""SQLite storage (stdlib sqlite3). All SQL lives here.

The schema is versioned with ``PRAGMA user_version``; ``MIGRATIONS[n]`` takes a database from version n to n + 1. Pages
are keyed on the wiki database name plus the normalized title; the watchlist uses page IDs. Methods arrive with the
features that use them: edit minutes and the watchlist so far.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_V1 = [
    """CREATE TABLE pageviews (
        wiki TEXT NOT NULL, page TEXT NOT NULL, day TEXT NOT NULL, views INTEGER NOT NULL,
        PRIMARY KEY (wiki, page, day))""",
    """CREATE TABLE baselines (
        wiki TEXT NOT NULL, page TEXT NOT NULL, median30 REAL NOT NULL, days INTEGER NOT NULL,
        computed_at TEXT NOT NULL, PRIMARY KEY (wiki, page))""",
    """CREATE TABLE watchlist (
        wiki TEXT NOT NULL, page_id INTEGER NOT NULL, title TEXT NOT NULL, added_at TEXT NOT NULL,
        PRIMARY KEY (wiki, page_id))""",
    """CREATE TABLE last_seen (
        wiki TEXT NOT NULL, page_id INTEGER NOT NULL, rev_id INTEGER NOT NULL, seen_at TEXT NOT NULL,
        PRIMARY KEY (wiki, page_id))""",
    """CREATE TABLE edit_minutes (
        wiki TEXT NOT NULL, page TEXT NOT NULL, minute TEXT NOT NULL, edits INTEGER NOT NULL,
        human_edits INTEGER NOT NULL, bytes_added INTEGER NOT NULL, bytes_removed INTEGER NOT NULL,
        PRIMARY KEY (wiki, page, minute))""",
]

# Version 2: what the watchlist needs beyond the v1 tables. ``unseen`` counts edits newer than last_seen.rev_id,
# ``last_rev_id`` is the newest revision known, ``deleted`` marks a page the wiki no longer has.
SCHEMA_V2 = [
    "ALTER TABLE watchlist ADD COLUMN unseen INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE watchlist ADD COLUMN last_rev_id INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE watchlist ADD COLUMN last_edit_at TEXT",
    "ALTER TABLE watchlist ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0",
    "UPDATE watchlist SET last_rev_id = coalesce((SELECT rev_id FROM last_seen s "
    "WHERE s.wiki = watchlist.wiki AND s.page_id = watchlist.page_id), 0)",
]

MIGRATIONS: list[list[str]] = [SCHEMA_V1, SCHEMA_V2]
SCHEMA_VERSION = len(MIGRATIONS)

_WATCH_COLUMNS = (
    "w.wiki, w.page_id, w.title, w.added_at, w.unseen, w.last_rev_id, coalesce(s.rev_id, 0), w.last_edit_at, w.deleted"
)
_WATCH_FROM = "FROM watchlist w LEFT JOIN last_seen s ON s.wiki = w.wiki AND s.page_id = w.page_id"


@dataclass(frozen=True, slots=True)
class WatchedPage:
    wiki: str
    page_id: int
    title: str
    added_at: datetime
    unseen: int  # edits newer than ``seen_rev_id`` that the app knows about
    last_rev_id: int  # the newest revision the app knows about
    seen_rev_id: int  # the revision the reader last looked at
    last_edit_at: datetime | None
    deleted: bool  # the wiki no longer has the page


def _watched(row: tuple[Any, ...]) -> WatchedPage:
    return WatchedPage(
        wiki=row[0],
        page_id=row[1],
        title=row[2],
        added_at=datetime.fromisoformat(row[3]),
        unseen=row[4],
        last_rev_id=row[5],
        seen_rev_id=row[6],
        last_edit_at=None if row[7] is None else datetime.fromisoformat(row[7]),
        deleted=bool(row[8]),
    )


class Store:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)  # autocommit; transactions are explicit
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")  # stays "memory" for :memory:, which is fine
        self._migrate()

    def _migrate(self) -> None:
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"database is version {version}; this wikihose understands up to {SCHEMA_VERSION}")
        for step in range(version, SCHEMA_VERSION):
            self._db.execute("BEGIN")
            try:
                for statement in MIGRATIONS[step]:
                    self._db.execute(statement)
                self._db.execute(f"PRAGMA user_version = {step + 1}")
                self._db.execute("COMMIT")
            except BaseException:
                self._rollback()
                raise

    def _rollback(self) -> None:
        with suppress(sqlite3.Error):  # nothing to undo if SQLite already ended the transaction
            self._db.execute("ROLLBACK")

    def schema_version(self) -> int:
        return int(self._db.execute("PRAGMA user_version").fetchone()[0])

    def add_edit_minute(
        self,
        wiki: str,
        page: str,
        minute: str,
        *,
        edits: int = 1,
        human_edits: int = 0,
        bytes_added: int = 0,
        bytes_removed: int = 0,
    ) -> None:
        """Add to the counters for one page in one UTC minute (an ISO minute such as 2026-11-07T14:05)."""
        with self._transaction():
            self._db.execute(
                "INSERT INTO edit_minutes (wiki, page, minute, edits, human_edits, bytes_added, bytes_removed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (wiki, page, minute) DO UPDATE SET "
                "edits = edits + excluded.edits, human_edits = human_edits + excluded.human_edits, "
                "bytes_added = bytes_added + excluded.bytes_added, "
                "bytes_removed = bytes_removed + excluded.bytes_removed",
                (wiki, page, minute, edits, human_edits, bytes_added, bytes_removed),
            )

    def edit_minute(self, wiki: str, page: str, minute: str) -> tuple[int, int, int, int] | None:
        """(edits, human_edits, bytes_added, bytes_removed) for one page-minute, or None."""
        row = self._db.execute(
            "SELECT edits, human_edits, bytes_added, bytes_removed FROM edit_minutes "
            "WHERE wiki = ? AND page = ? AND minute = ?",
            (wiki, page, minute),
        ).fetchone()
        return None if row is None else (row[0], row[1], row[2], row[3])

    def prune_edit_minutes(self, before: str) -> int:
        """Delete rows for minutes earlier than ``before``; returns how many."""
        with self._transaction():
            return self._db.execute("DELETE FROM edit_minutes WHERE minute < ?", (before,)).rowcount

    def add_watch(self, wiki: str, page_id: int, title: str, now: datetime, rev_id: int) -> bool:
        """Watch a page, already seen up to ``rev_id``. Returns False when it was already watched."""
        with self._transaction():
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO watchlist (wiki, page_id, title, added_at, last_rev_id) VALUES (?, ?, ?, ?, ?)",
                (wiki, page_id, title, now.isoformat(), rev_id),
            )
            if cursor.rowcount == 0:
                return False
            self._db.execute(
                "INSERT OR REPLACE INTO last_seen (wiki, page_id, rev_id, seen_at) VALUES (?, ?, ?, ?)",
                (wiki, page_id, rev_id, now.isoformat()),
            )
            return True

    def remove_watch(self, wiki: str, page_id: int) -> bool:
        with self._transaction():
            self._db.execute("DELETE FROM last_seen WHERE wiki = ? AND page_id = ?", (wiki, page_id))
            return (
                self._db.execute("DELETE FROM watchlist WHERE wiki = ? AND page_id = ?", (wiki, page_id)).rowcount > 0
            )

    def watched_pages(self) -> list[WatchedPage]:
        """Every watched page, oldest first."""
        rows = self._db.execute(f"SELECT {_WATCH_COLUMNS} {_WATCH_FROM} ORDER BY w.added_at, w.wiki, w.page_id")
        return [_watched(row) for row in rows]

    def watched_page(self, wiki: str, page_id: int) -> WatchedPage | None:
        row = self._db.execute(
            f"SELECT {_WATCH_COLUMNS} {_WATCH_FROM} WHERE w.wiki = ? AND w.page_id = ?", (wiki, page_id)
        ).fetchone()
        return None if row is None else _watched(row)

    def rename_watch(self, wiki: str, page_id: int, title: str) -> None:
        with self._transaction():
            self._db.execute("UPDATE watchlist SET title = ? WHERE wiki = ? AND page_id = ?", (title, wiki, page_id))

    def set_watch_state(
        self, wiki: str, page_id: int, *, unseen: int, last_rev_id: int, last_edit_at: datetime | None, deleted: bool
    ) -> None:
        with self._transaction():
            self._db.execute(
                "UPDATE watchlist SET unseen = ?, last_rev_id = ?, last_edit_at = ?, deleted = ? "
                "WHERE wiki = ? AND page_id = ?",
                (
                    unseen,
                    last_rev_id,
                    None if last_edit_at is None else last_edit_at.isoformat(),
                    int(deleted),
                    wiki,
                    page_id,
                ),
            )

    def mark_watch_seen(self, wiki: str, page_id: int, now: datetime) -> None:
        """The reader has looked at everything up to the newest known revision."""
        with self._transaction():
            self._db.execute(
                "INSERT OR REPLACE INTO last_seen (wiki, page_id, rev_id, seen_at) "
                "SELECT wiki, page_id, last_rev_id, ? FROM watchlist WHERE wiki = ? AND page_id = ?",
                (now.isoformat(), wiki, page_id),
            )
            self._db.execute("UPDATE watchlist SET unseen = 0 WHERE wiki = ? AND page_id = ?", (wiki, page_id))

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._rollback()
            raise

    def close(self) -> None:
        self._db.close()
