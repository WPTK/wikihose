"""SQLite storage (stdlib sqlite3). All SQL lives here.

The schema is versioned with ``PRAGMA user_version``; ``MIGRATIONS[n]`` takes a database from version n to n + 1. Pages
are keyed on the wiki database name plus the normalized title; the watchlist uses page IDs. Only the methods PR 2 needs
exist; the watchlist and pageview methods arrive with the features that use them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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

MIGRATIONS: list[list[str]] = [SCHEMA_V1]
SCHEMA_VERSION = len(MIGRATIONS)


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
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

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

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def close(self) -> None:
        self._db.close()
