import sqlite3
from pathlib import Path

from clankops.db import connect
from clankops.store import open_store


def test_concurrent_readers_see_committed_state(tmp_path: Path) -> None:
    db = tmp_path / "wal.db"
    writer = open_store(db, actor="w")
    writer.register_clank("oem-radar")
    reader = connect(db)
    slugs = [r[0] for r in reader.execute("SELECT slug FROM clanks").fetchall()]
    assert slugs == ["oem-radar"]
    writer.start_mission("oem-radar", "work")
    displays = [r[0] for r in reader.execute("SELECT display_id FROM missions").fetchall()]
    assert displays == ["COPS-000001"]
    # second reader connection
    reader2 = sqlite3.connect(str(db), timeout=30)
    reader2.row_factory = sqlite3.Row
    reader2.execute("PRAGMA journal_mode = WAL")
    count = reader2.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count >= 2
    reader.close()
    reader2.close()
    writer.conn.close()


def test_brief_does_not_invent_next_action(store) -> None:
    store.register_clank("oem-radar")
    store.start_mission("oem-radar", "something")
    brief = store.brief("oem-radar")
    assert brief["next_action"] is None
    from clankops.brief import format_brief

    text = format_brief(brief)
    assert "refusing to invent one" in text
