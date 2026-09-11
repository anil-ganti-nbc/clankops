import sqlite3
from pathlib import Path

import pytest

from clankops.clock import FrozenClock
from clankops.db import connect, current_schema_version
from clankops.events import (
    LEDGER_ORDER_SQL,
    assert_append_only,
    assert_ledger_seq_immutable,
    copy_events,
    list_events,
)
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.schema import MIGRATIONS
from clankops.store import open_store
from tests.conftest import FROZEN


def test_ledger_seq_monotonic_and_unique(store) -> None:
    store.register_clank("oem-radar")
    store.start_mission("oem-radar", "one")
    store.start_mission("oem-radar", "two")
    seqs = [
        int(r[0])
        for r in store.conn.execute(
            f"SELECT ledger_seq FROM events {LEDGER_ORDER_SQL}"
        )
    ]
    assert seqs == list(range(1, len(seqs) + 1))
    assert len(seqs) == len(set(seqs))
    head = store.conn.execute("SELECT next_seq FROM ledger_head WHERE id = 1").fetchone()[0]
    assert int(head) == max(seqs) + 1


def test_ledger_seq_immutable(store) -> None:
    store.register_clank("oem-radar")
    assert_append_only(store.conn)
    assert_ledger_seq_immutable(store.conn)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE events SET ledger_seq = 99 WHERE ledger_seq = 1")
    store.conn.rollback()


def test_same_timestamp_replay_uses_ledger_seq(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "same-ts")
    task = store.add_task(m["display_id"], "write parser")
    store.transition_task(task["task_id"], "DONE")
    events = list_events(store.conn, mission_id=m["mission_id"])
    timestamps = {e.ts_utc for e in events}
    assert len(timestamps) == 1
    created = next(e for e in events if e.event_type == "TASK_CREATED")
    done = next(e for e in events if e.event_type == "TASK_STATE_CHANGED")
    assert created.ledger_seq < done.ledger_seq
    before = dump_projection_state(store.conn)
    rebuild_projections(store.conn)
    after = dump_projection_state(store.conn)
    assert before == after
    row = store.resolve_task(task["task_id"])
    assert row["state"] == "DONE"


def test_v1_events_migrate_ledger_seq(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(MIGRATIONS[0][2])
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at_utc) VALUES (1, 'foundation0_ledger', '2026-09-10T00:00:00.000000Z')"
    )
    conn.execute(
        """
        INSERT INTO events (
            event_id, ts_utc, clank_id, mission_id, session_id,
            event_type, actor, source, payload_json, provenance_json, schema_version
        ) VALUES
        ('bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb', '2026-09-10T04:00:00.000000Z', NULL, NULL, NULL,
         'CLANK_REGISTERED', 'system', 'RECONSTRUCTED', '{"slug":"oem-radar","display_name":"OEM Radar","aliases":[],"refs":[],"lifecycle":"UNKNOWN"}', '{}', 1),
        ('aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa', '2026-09-10T04:00:00.000000Z', NULL, NULL, NULL,
         'CLANK_REGISTERED', 'system', 'RECONSTRUCTED', '{"slug":"watch-clank","display_name":"Watch","aliases":[],"refs":[],"lifecycle":"UNKNOWN"}', '{}', 1)
        """
    )
    conn.commit()
    conn.close()

    conn = connect(db, clock=FrozenClock(FROZEN))
    assert current_schema_version(conn) == 3
    rows = conn.execute(f"SELECT event_id, ledger_seq FROM events {LEDGER_ORDER_SQL}").fetchall()
    assert [r["ledger_seq"] for r in rows] == [1, 2]
    # F0 order was ts_utc, event_id: aaaa before bbbb
    assert rows[0]["event_id"].startswith("aaaaaaaa")
    assert rows[1]["event_id"].startswith("bbbbbbbb")
    head = conn.execute("SELECT next_seq FROM ledger_head WHERE id = 1").fetchone()[0]
    assert int(head) == 3
    conn.close()


def test_copy_events_preserves_ledger_seq(tmp_path, store) -> None:
    store.register_clank("oem-radar")
    store.start_mission("oem-radar", "x")
    src_seqs = [
        int(r[0])
        for r in store.conn.execute("SELECT ledger_seq FROM events ORDER BY ledger_seq")
    ]
    other = open_store(tmp_path / "b.db", actor="tester", clock=store.clock)
    copy_events(store.conn, other.conn)
    dst_seqs = [
        int(r[0])
        for r in other.conn.execute("SELECT ledger_seq FROM events ORDER BY ledger_seq")
    ]
    assert dst_seqs == src_seqs
    other.conn.close()
