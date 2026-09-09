import sqlite3

import pytest

from clankops.errors import AppendOnlyViolation
from clankops.events import assert_append_only, list_events
from clankops.projections import dump_projection_state, rebuild_projections


def _populate(store) -> None:
    store.register_clank("oem-radar", aliases=["radar"], local_path=r"C:\Clanks\oem-radar")
    m = store.start_mission("oem-radar", "Add source")
    store.add_task(m["display_id"], "implement parser")
    store.add_feature("oem-radar", "parser")
    store.add_decision(m["display_id"], "use YAML descriptors", why="matches OEM Radar ADR-2")
    store.add_blocker(m["display_id"], "need fixtures")
    store.record_checkpoint(
        m["display_id"],
        current_work="parser",
        next_action="write fixtures",
        branch="main",
        head="abc123",
    )
    store.pause_mission(m["display_id"])


def test_events_are_append_only(store) -> None:
    _populate(store)
    events = list_events(store.conn)
    assert len(events) >= 2
    first = events[0]
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "UPDATE events SET actor = 'mutated' WHERE event_id = ?",
            (first.event_id,),
        )
    store.conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM events WHERE event_id = ?", (first.event_id,))
    store.conn.rollback()
    assert_append_only(store.conn)
    # original actor preserved
    row = store.conn.execute(
        "SELECT actor FROM events WHERE event_id = ?", (first.event_id,)
    ).fetchone()
    assert row[0] != "mutated"


def test_provenance_preserved(store) -> None:
    store.register_clank(
        "watch-clank",
        source="RECONSTRUCTED",
        actor="system",
    )
    event = list_events(store.conn)[0]
    assert event.source == "RECONSTRUCTED"
    assert event.actor == "system"
    assert event.provenance["recorder"] == "clankops.store"


def test_projection_rebuild_is_deterministic(store) -> None:
    _populate(store)
    before = dump_projection_state(store.conn)
    count = rebuild_projections(store.conn)
    after = dump_projection_state(store.conn)
    assert count == len(list_events(store.conn))
    assert before == after


def test_rebuild_from_events_only(tmp_path, store) -> None:
    _populate(store)
    expected = dump_projection_state(store.conn)
    events = store.conn.execute("SELECT * FROM events ORDER BY ts_utc, event_id").fetchall()
    fresh = tmp_path / "fresh.db"
    from clankops.store import open_store

    other = open_store(fresh, actor="tester", clock=store.clock)
    for row in events:
        other.conn.execute(
            """
            INSERT INTO events (
                event_id, ts_utc, clank_id, mission_id, session_id,
                event_type, actor, source, payload_json, provenance_json,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(row),
        )
    other.conn.commit()
    rebuild_projections(other.conn)
    got = dump_projection_state(other.conn)
    assert got == expected
    other.conn.close()
