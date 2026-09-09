from pathlib import Path

from clankops.db import connect, current_schema_version


def test_database_creation_and_wal(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    conn = connect(db)
    assert db.exists()
    assert current_schema_version(conn) == 1
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    fks = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fks == 1
    conn.close()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    conn = connect(db)
    version = current_schema_version(conn)
    conn.close()
    conn = connect(db)
    assert current_schema_version(conn) == version
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 1
    conn.close()


def test_persistence_across_reconnect(tmp_path: Path) -> None:
    from clankops.store import open_store

    db = tmp_path / "persist.db"
    s = open_store(db, actor="a")
    s.register_clank("oem-radar", display_name="OEM Radar")
    s.conn.close()
    s2 = open_store(db, actor="a")
    row = s2.resolve_clank("oem-radar")
    assert row["display_name"] == "OEM Radar"
    s2.conn.close()
