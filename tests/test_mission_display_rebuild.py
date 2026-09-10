from pathlib import Path

from clankops.events import copy_events
from clankops.projections import rebuild_projections
from clankops.store import open_store


def test_next_mission_id_after_event_only_reconstruction(tmp_path: Path, store) -> None:
    store.register_clank("oem-radar")
    ids = []
    for i in range(5):
        m = store.start_mission("oem-radar", f"objective {i+1}")
        ids.append(m["display_id"])
        store.complete_mission(m["display_id"])
    assert ids == [
        "COPS-000001",
        "COPS-000002",
        "COPS-000003",
        "COPS-000004",
        "COPS-000005",
    ]

    fresh = tmp_path / "replay.db"
    other = open_store(fresh, actor="tester", clock=store.clock)
    copied = copy_events(store.conn, other.conn)
    assert copied >= 5
    rebuild_projections(other.conn)

    seq = other.conn.execute(
        "SELECT next_value FROM id_sequences WHERE name = 'mission_display'"
    ).fetchone()[0]
    assert int(seq) == 6

    nxt = other.start_mission("oem-radar", "after rebuild")
    assert nxt["display_id"] == "COPS-000006"
    existing = {
        r[0]
        for r in other.conn.execute("SELECT display_id FROM missions").fetchall()
    }
    assert existing == {
        "COPS-000001",
        "COPS-000002",
        "COPS-000003",
        "COPS-000004",
        "COPS-000005",
        "COPS-000006",
    }
    other.conn.close()
