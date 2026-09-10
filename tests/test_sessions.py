from datetime import datetime, timedelta, timezone

from clankops.clock import TickingClock
from clankops.events import copy_events, list_events
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.store import open_store


def _open(store):
    rows = store.conn.execute(
        "SELECT session_id FROM sessions WHERE ended_utc IS NULL"
    ).fetchall()
    return [r[0] for r in rows]


def test_start_opens_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work", actor="cursor")
    sessions = store.list_sessions(m["display_id"])
    assert len(sessions) == 1
    assert sessions[0]["ended_utc"] is None
    assert sessions[0]["actor"] == "cursor"
    assert m["session_id"] == sessions[0]["session_id"]


def test_pause_closes_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    store.pause_mission(m["display_id"])
    sessions = store.list_sessions(m["display_id"])
    assert len(sessions) == 1
    assert sessions[0]["ended_utc"] is not None
    assert _open(store) == []


def test_block_closes_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    store.block_mission(m["display_id"])
    assert store.list_sessions(m["display_id"])[0]["ended_utc"] is not None
    assert store.resolve_mission(m["display_id"])["state"] == "BLOCKED"


def test_complete_closes_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    store.complete_mission(m["display_id"])
    assert store.list_sessions(m["display_id"])[0]["ended_utc"] is not None


def test_abandon_closes_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    store.abandon_mission(m["display_id"])
    assert store.list_sessions(m["display_id"])[0]["ended_utc"] is not None


def test_resume_opens_new_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work", actor="cursor")
    first = m["session_id"]
    store.pause_mission(m["display_id"])
    resumed = store.resume_mission(m["display_id"], actor="cursor")
    sessions = store.list_sessions(m["display_id"])
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == first
    assert sessions[0]["ended_utc"] is not None
    assert resumed["session_id"] != first
    assert sessions[1]["ended_utc"] is None
    assert sessions[1]["session_id"] == resumed["session_id"]


def test_historical_sessions_remain_and_duration(tmp_path) -> None:
    clock = TickingClock(datetime(2026, 9, 10, 4, 0, 0, tzinfo=timezone.utc), timedelta(seconds=5))
    store = open_store(tmp_path / "dur.db", actor="cursor", clock=clock)
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "timed")
    store.pause_mission(m["display_id"])
    row = store.list_sessions(m["display_id"])[0]
    start = datetime.strptime(row["started_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    end = datetime.strptime(row["ended_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    assert (end - start).total_seconds() >= 5
    store.conn.close()


def test_checkpoint_and_mutations_attributed_to_session(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    sid = m["session_id"]
    store.default_session_id = sid
    cp = store.record_checkpoint(m["display_id"], current_work="code", next_action="tests")
    task = store.add_task(m["display_id"], "write tests")
    feat = store.add_feature("oem-radar", "ledger_seq")
    dec = store.add_decision(m["display_id"], "Use ledger_seq", why="canonical order")
    blk = store.add_blocker(m["display_id"], "need review")
    art = store.attach_artifact(mission=m["display_id"], kind="COMMIT", ref="abc123")
    events = list_events(store.conn, mission_id=m["mission_id"])
    by_type = {e.event_type: e for e in events}
    assert by_type["CHECKPOINT_RECORDED"].session_id == sid
    assert by_type["TASK_CREATED"].session_id == sid
    assert by_type["DECISION_RECORDED"].session_id == sid
    assert by_type["BLOCKER_ADDED"].session_id == sid
    assert by_type["ARTIFACT_ATTACHED"].session_id == sid
    feature_events = [e for e in list_events(store.conn) if e.event_type == "FEATURE_ADDED"]
    assert feature_events[-1].session_id == sid
    assert cp["session_id"] == sid
    store.transition_task(task["task_id"], "DONE")
    done = next(e for e in list_events(store.conn) if e.event_type == "TASK_STATE_CHANGED")
    assert done.session_id == sid
    assert feat["feature_id"]
    assert dec["decision_id"]
    assert blk["blocker_id"]
    assert art["artifact_id"]


def test_concurrent_sessions_on_one_mission(store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "shared", actor="cursor")
    cursor_sid = m["session_id"]
    grok = store.start_session(m["display_id"], actor="grok")
    grok_sid = grok["session_id"]
    assert cursor_sid != grok_sid
    open_ids = _open(store)
    assert set(open_ids) == {cursor_sid, grok_sid}

    store.record_checkpoint(m["display_id"], current_work="cursor work", session_id=cursor_sid)
    store.record_checkpoint(m["display_id"], current_work="grok work", session_id=grok_sid)
    cps = list_events(store.conn, mission_id=m["mission_id"])
    cursor_cp = [e for e in cps if e.event_type == "CHECKPOINT_RECORDED" and e.session_id == cursor_sid]
    grok_cp = [e for e in cps if e.event_type == "CHECKPOINT_RECORDED" and e.session_id == grok_sid]
    assert len(cursor_cp) == 1
    assert len(grok_cp) == 1
    assert cursor_cp[0].payload["current_work"] == "cursor work"
    assert grok_cp[0].payload["current_work"] == "grok work"

    store.pause_mission(m["display_id"])
    assert _open(store) == []
    sessions = store.list_sessions(m["display_id"])
    assert all(s["ended_utc"] for s in sessions)


def test_rebuild_preserves_session_history(tmp_path, store) -> None:
    store.register_clank("oem-radar")
    m = store.start_mission("oem-radar", "work")
    store.record_checkpoint(m["display_id"], next_action="pause")
    store.pause_mission(m["display_id"])
    store.resume_mission(m["display_id"])
    before = dump_projection_state(store.conn)
    sessions_before = [dict(r) for r in store.conn.execute("SELECT * FROM sessions").fetchall()]
    assert len(sessions_before) == 2
    assert sessions_before[0]["ended_utc"] is not None
    assert sessions_before[1]["ended_utc"] is None

    other = open_store(tmp_path / "replay.db", actor=store.default_actor, clock=store.clock)
    copy_events(store.conn, other.conn)
    rebuild_projections(other.conn)
    after = dump_projection_state(other.conn)
    assert after["sessions"] == before["sessions"]
    assert after == before
    other.conn.close()


def test_reconstructed_events_may_have_no_session(store) -> None:
    store.register_clank("oem-radar", source="RECONSTRUCTED", actor="system")
    ev = list_events(store.conn)[0]
    assert ev.session_id is None
    assert ev.source == "RECONSTRUCTED"
