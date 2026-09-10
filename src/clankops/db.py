"""SQLite connection, WAL mode, and migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from clankops.clock import Clock, SystemClock, isoformat_utc
from clankops.schema import MIGRATIONS

DEFAULT_PRAGMAS = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
    "PRAGMA busy_timeout = 5000;",
    "PRAGMA synchronous = NORMAL;",
)


def connect(path: str | Path, *, clock: Clock | None = None) -> sqlite3.Connection:
    """Open (or create) a ClankOps database and apply pending migrations."""
    clock = clock or SystemClock()
    db_path = Path(path)
    if db_path.parent.as_posix() not in {"", "."}:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    for pragma in DEFAULT_PRAGMAS:
        conn.execute(pragma)
    apply_migrations(conn, clock=clock)
    return conn


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing ledger without migrating or writing."""
    db_path = Path(path)
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    uri = db_path.resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def apply_migrations(conn: sqlite3.Connection, *, clock: Clock | None = None) -> list[int]:
    clock = clock or SystemClock()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at_utc TEXT NOT NULL
        )
        """
    )
    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    newly: list[int] = []
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        # schema_migrations is created both here and in migration 1; skip
        # the duplicate CREATE TABLE in the migration body by executing
        # statements individually and ignoring that one table.
        _exec_migration_sql(conn, sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at_utc) VALUES (?, ?, ?)",
            (version, name, isoformat_utc(clock.now())),
        )
        newly.append(version)
    conn.commit()
    return newly


def _exec_migration_sql(conn: sqlite3.Connection, sql: str) -> None:
    conn.executescript(sql)


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])
