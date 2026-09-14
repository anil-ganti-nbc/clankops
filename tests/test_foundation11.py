"""Foundation 11: Mission reconciliation and Session-close vs handoff."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from clankops.agent import admit_agent, prepare_agent
from clankops.attention import attention_report
from clankops.brief import format_history
from clankops.cli import main
from clankops.clock import FrozenClock, isoformat_utc
from clankops.enums import EventSource, EventType, MissionState
from clankops.errors import ClankOpsError, InvalidTransitionError, NotFoundError, ValidationError
from clankops.events import list_events
from clankops.launch import launch_agent
from clankops.process import (
    HANDOFF_MISSING,
    HANDOFF_RECORDED,
    HANDOFF_UNKNOWN,
    SESSION_CLOSED,
    SESSION_OPEN,
    derive_handoff_status,
    session_process_view,
)
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.readmodel import dossier, event_summary, ledger_fingerprint, observe_session
from clankops.reconciliation import reconcile_mission
from clankops.resume import resume_packet
from clankops.store import open_store
from clankops.terminal import _dossier_html, _session_dossier_html

from test_foundation2 import T0
from test_foundation3 import _seed
from test_foundation9 import FakeProc, _event_count, _paused
from test_foundation10 import _launch

MERGE = "d1a6f3b886b5044ffd8e530a52d912e1fae973c2"
EVIDENCE = ["github:pr:6", f"github:commit:{MERGE}"]
REASON = (
    "Foundation 5 implementation was reviewed and merged, but the "
    "ClankOps Mission remained PAUSED."
)
BANNED_AUTHORITY = ("motherclank", "quartermaster", "standards clank")


class MovableClock:
    def __init__(self, instant) -> None:
        self.instant = instant

    def now(self):
        return self.instant


def _types(store) -> list[str]:
    return [event.event_type for event in list_events(store.conn)]


def _of(store, event_type: str) -> list:
    return [event for event in list_events(store.conn) if event.event_type == event_type]


def _reconcile(store, mission, **kwargs):
    token = mission["display_id"] if isinstance(mission, dict) else mission
    return reconcile_mission(
        store,
        token,
        to_state=kwargs.get("to_state", MissionState.COMPLETED),
        reason=kwargs.get("reason", REASON),
        evidence=kwargs.get("evidence", EVIDENCE),
        basis=kwargs.get("basis", "github"),
        actor=kwargs.get("actor", "user"),
        source=kwargs.get("source", EventSource.USER),
        evidence_occurred_at=kwargs.get("evidence_occurred_at"),
    )


def test_paused_to_completed_without_active(tmp_path: Path) -> None:
    store = open_store(tmp_path / "paused.db", actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    before_started = _event_count(store, EventType.SESSION_STARTED)
    before_ended = _event_count(store, EventType.SESSION_ENDED)
    before_checkpoint = _event_count(store, EventType.CHECKPOINT_RECORDED)
    paused_events = [
        event
        for event in _of(store, EventType.MISSION_STATE_CHANGED)
        if event.payload.get("to_state") == MissionState.PAUSED
    ]
    assert paused_events
    paused_blob = json.dumps(paused_events[0].payload, sort_keys=True)
    paused_id = paused_events[0].event_id
    paused_ts = paused_events[0].ts_utc

    result = _reconcile(store, mission)
    row = store.resolve_mission(mission["display_id"])
    assert row["state"] == MissionState.COMPLETED
    assert result["from_state"] == MissionState.PAUSED
    assert result["to_state"] == MissionState.COMPLETED
    assert result["session_id"] is None
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 1
    assert _event_count(store, EventType.SESSION_STARTED) == before_started
    assert _event_count(store, EventType.SESSION_ENDED) == before_ended
    assert _event_count(store, EventType.CHECKPOINT_RECORDED) == before_checkpoint
    assert not any(
        event.payload.get("to_state") == MissionState.ACTIVE
        for event in _of(store, EventType.MISSION_STATE_CHANGED)
        if event.payload.get("from_state") == MissionState.PAUSED
    )
    still = store.conn.execute(
        "SELECT * FROM events WHERE event_id = ?", (paused_id,)
    ).fetchone()
    assert still["ts_utc"] == paused_ts
    assert json.loads(still["payload_json"]) == json.loads(paused_blob)
    unfinished = store.unfinished_missions("oem-radar")
    assert all(item["display_id"] != mission["display_id"] for item in unfinished)
    store.conn.close()


def test_blocked_to_completed_without_active(tmp_path: Path) -> None:
    store = open_store(tmp_path / "blocked.db", actor="user", clock=FrozenClock(T0))
    mission = _seed(store)
    store.block_mission(mission["display_id"])
    before_started = _event_count(store, EventType.SESSION_STARTED)
    result = _reconcile(store, mission)
    assert result["from_state"] == MissionState.BLOCKED
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.COMPLETED
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 1
    assert _event_count(store, EventType.SESSION_STARTED) == before_started
    assert not any(
        event.payload.get("to_state") == MissionState.ACTIVE
        for event in _of(store, EventType.MISSION_STATE_CHANGED)
        if event.payload.get("from_state") == MissionState.BLOCKED
    )
    store.conn.close()


def test_planned_to_completed_without_active(tmp_path: Path) -> None:
    store = open_store(tmp_path / "planned.db", actor="user", clock=FrozenClock(T0))
    store.register_clank("oem-radar")
    mission = store.start_mission(
        "oem-radar", "planned objective", state=MissionState.PLANNED
    )
    assert mission["state"] == MissionState.PLANNED
    assert _event_count(store, EventType.SESSION_STARTED) == 0
    result = _reconcile(store, mission)
    assert result["from_state"] == MissionState.PLANNED
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.COMPLETED
    assert _event_count(store, EventType.SESSION_STARTED) == 0
    assert _event_count(store, EventType.SESSION_ENDED) == 0
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 1
    store.conn.close()


def test_reconciliation_requires_explicit_inputs(tmp_path: Path) -> None:
    store = open_store(tmp_path / "req.db", actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    with pytest.raises(NotFoundError):
        _reconcile(store, "COPS-009999")
    with pytest.raises(ValidationError, match="reason"):
        _reconcile(store, mission, reason="  ")
    with pytest.raises(ValidationError, match="evidence"):
        _reconcile(store, mission, evidence=[])
    with pytest.raises(ValidationError, match="basis"):
        _reconcile(store, mission, basis="vibes")
    store.conn.close()


def test_reconciliation_timestamp_is_observation_time(tmp_path: Path) -> None:
    clock = MovableClock(T0)
    store = open_store(tmp_path / "ts.db", actor="user", clock=clock)
    mission = _paused(store)
    later = T0 + timedelta(days=4)
    evidence_at = T0 + timedelta(days=1)
    clock.instant = later
    result = _reconcile(
        store, mission, evidence_occurred_at=isoformat_utc(evidence_at)
    )
    event = _of(store, EventType.MISSION_STATE_RECONCILED)[0]
    assert event.ts_utc == isoformat_utc(later)
    assert result["observed_at"] == isoformat_utc(later)
    assert result["evidence_occurred_at"] == isoformat_utc(evidence_at)
    assert event.payload["evidence_occurred_at"] == isoformat_utc(evidence_at)
    assert event.ts_utc != event.payload["evidence_occurred_at"]
    assert event.source == EventSource.USER
    assert event.actor == "user"
    assert {item["source"] for item in event.payload["evidence"]} == {"GITHUB"}
    store.conn.close()


def test_rebuild_keeps_reconciled_state(tmp_path: Path) -> None:
    store = open_store(tmp_path / "rebuild.db", actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    _reconcile(store, mission)
    before = dump_projection_state(store.conn)
    fp = ledger_fingerprint(store)
    rebuild_projections(store.conn)
    after = dump_projection_state(store.conn)
    assert after == before
    assert ledger_fingerprint(store) == fp
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.COMPLETED
    recs = store.conn.execute("SELECT * FROM mission_reconciliations").fetchall()
    assert len(recs) == 1
    store.conn.close()


def test_timeline_distinguishes_reconciliation_from_transition(tmp_path: Path) -> None:
    store = open_store(tmp_path / "tl.db", actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    _reconcile(store, mission, evidence_occurred_at="2026-09-11T12:00:00Z")
    event = _of(store, EventType.MISSION_STATE_RECONCILED)[0]
    summary = event_summary(event)
    assert summary.startswith("RECONCILED PAUSED -> COMPLETED")
    assert "reason:" in summary
    assert "PR #6" in summary
    assert "reconciled:" in summary
    assert "evidence occurred:" in summary
    history = format_history(list_events(store.conn, clank_id=mission["clank_id"]))
    assert "MISSION_STATE_RECONCILED" in history
    assert "RECONCILED PAUSED -> COMPLETED" in history
    payload = dossier(store, "oem-radar", now=T0, include_github=False)
    html = _dossier_html(payload)
    assert "MISSION_STATE_RECONCILED" in html
    assert "[RECONCILED]" in html
    assert "PAUSED -> COMPLETED" in html
    store.conn.close()


def test_reconciled_mission_clears_prepare_ambiguity(tmp_path: Path) -> None:
    store = open_store(tmp_path / "amb.db", actor="cursor", clock=FrozenClock(T0))
    first = _paused(store)
    second = store.start_mission("oem-radar", "Foundation 11 work")
    store.pause_mission(second["display_id"])
    prepared = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    assert prepared["admission"]["status"] == "AMBIGUOUS"
    before = resume_packet(store, "oem-radar", include_github=False, now=T0)
    _reconcile(store, first)
    packet = resume_packet(store, "oem-radar", include_github=False, now=T0)
    assert packet["context_fingerprint"] != before["context_fingerprint"]
    ids = {row["mission"] for row in packet["unfinished_missions"]}
    assert first["display_id"] not in ids
    assert ids == {second["display_id"]}
    assert packet["admission"]["status"] == "RESUMABLE"
    assert packet["mission_reconciliations"]
    prepared = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    assert prepared["admission"]["status"] == "RESUMABLE"
    later = resume_packet(
        store, "oem-radar", include_github=False, now=T0 + timedelta(hours=5)
    )
    assert later["context_fingerprint"] == packet["context_fingerprint"]
    store.conn.close()


def test_ordinary_active_complete_unchanged(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ordinary.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    done = store.complete_mission(mission["display_id"])
    assert done["state"] == MissionState.COMPLETED
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 0
    changed = [
        event
        for event in _of(store, EventType.MISSION_STATE_CHANGED)
        if event.payload.get("to_state") == MissionState.COMPLETED
    ]
    assert len(changed) == 1
    assert changed[0].payload.get("from_state") == MissionState.ACTIVE
    store.conn.close()


def test_terminal_history_is_not_casually_rewritten(tmp_path: Path) -> None:
    store = open_store(tmp_path / "termstate.db", actor="user", clock=FrozenClock(T0))
    mission = _seed(store)
    store.complete_mission(mission["display_id"])
    with pytest.raises(InvalidTransitionError):
        _reconcile(store, mission)
    abandoned = store.start_mission("oem-radar", "abandon me")
    store.abandon_mission(abandoned["display_id"])
    with pytest.raises(InvalidTransitionError):
        _reconcile(store, abandoned)
    active = store.start_mission("oem-radar", "still active")
    with pytest.raises(InvalidTransitionError, match="ordinary"):
        _reconcile(store, active)
    paused = store.start_mission("oem-radar", "pause then ordinary complete")
    store.pause_mission(paused["display_id"])
    with pytest.raises(InvalidTransitionError):
        store.complete_mission(paused["display_id"])
    store.conn.close()


def test_session_close_is_not_handoff_recorded(tmp_path: Path) -> None:
    store = open_store(tmp_path / "close.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    session = store.resolve_session(mission["session_id"])
    store.end_session(session["session_id"])
    closed = store.resolve_session(session["session_id"])
    view = session_process_view(store, closed, now=T0)
    assert view["session"] == SESSION_CLOSED
    assert view["handoff"] == HANDOFF_UNKNOWN
    assert derive_handoff_status(store, closed) == HANDOFF_UNKNOWN
    observed = observe_session(store, {"session_id": closed["session_id"], "ended_utc": closed["ended_utc"]}, now=T0)
    assert observed["handoff"] == HANDOFF_UNKNOWN
    html = _session_dossier_html(observed)
    assert "[CLOSED]" in html
    assert "[RECORDED]" not in html
    assert "[UNKNOWN]" in html
    store.conn.close()


def test_canonical_handoff_is_distinguishable(tmp_path: Path) -> None:
    store = open_store(tmp_path / "handoff.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    session_id = mission["session_id"]
    open_view = session_process_view(store, store.resolve_session(session_id), now=T0)
    assert open_view["session"] == SESSION_OPEN
    assert open_view["handoff"] == HANDOFF_MISSING
    store.handoff_mission(mission["display_id"], MissionState.PAUSED, current_work="pausing")
    closed = store.resolve_session(session_id)
    view = session_process_view(store, closed, now=T0)
    assert view["session"] == SESSION_CLOSED
    assert view["handoff"] == HANDOFF_RECORDED
    handoffs = _of(store, EventType.HANDOFF_RECORDED)
    assert len(handoffs) == 1
    assert handoffs[0].session_id == session_id
    assert handoffs[0].payload.get("to_state") == MissionState.PAUSED
    before = dump_projection_state(store.conn)
    rebuild_projections(store.conn)
    assert dump_projection_state(store.conn) == before
    assert derive_handoff_status(store, store.resolve_session(session_id)) == HANDOFF_RECORDED
    store.conn.close()


def test_complete_without_checkpoint_is_handoff_unknown(tmp_path: Path) -> None:
    store = open_store(tmp_path / "nocheck.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    session_id = mission["session_id"]
    store.complete_mission(mission["display_id"])
    closed = store.resolve_session(session_id)
    assert derive_handoff_status(store, closed) == HANDOFF_UNKNOWN
    store.conn.close()


def test_attention_remains_derived_zero_write(tmp_path: Path) -> None:
    store = open_store(tmp_path / "attn.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    before = ledger_fingerprint(store)
    attention_report(store, "oem-radar", now=T0, include_github=False)
    assert ledger_fingerprint(store) == before
    _reconcile(store, mission)
    after = ledger_fingerprint(store)
    attention_report(store, "oem-radar", now=T0, include_github=False)
    assert ledger_fingerprint(store) == after
    store.conn.close()


def test_foundation8_admit_behaviour_unchanged(tmp_path: Path) -> None:
    store = open_store(tmp_path / "admit.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    prepared = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    assert prepared["admission"]["status"] == "RESUMABLE"
    before = ledger_fingerprint(store)
    admitted = admit_agent(
        store,
        "oem-radar",
        actor="cursor",
        include_github=False,
        now=T0,
        expect_context=prepared["context_fingerprint"],
        mission=mission["display_id"],
    )
    assert admitted["session_id"]
    assert ledger_fingerprint(store)["event_count"] == before["event_count"] + 2
    store.conn.close()


def test_foundation9_fresh_session_same_actor_still_refuses(tmp_path: Path) -> None:
    store = open_store(tmp_path / "f9.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    runner = FakeProc()
    first = launch_agent(
        store,
        "oem-radar",
        actor="cursor",
        launcher="cursor",
        command=["agent"],
        include_github=False,
        now=T0,
        environ={},
        runner=runner,
    )
    assert first["session_id"]
    with pytest.raises(ValidationError, match="open Session already exists"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=["agent"],
            include_github=False,
            now=T0,
            environ={},
            runner=FakeProc(),
        )
    store.conn.close()


def test_foundation10_process_exit_observability_remains(tmp_path: Path) -> None:
    store = open_store(tmp_path / "f10.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = _launch(store, returncode=0)
    session = store.resolve_session(result["session_id"])
    assert session["ended_utc"] is None
    view = session_process_view(store, session, now=T0)
    assert view["status"] == "EXITED"
    assert view["session"] == SESSION_OPEN
    assert view["handoff"] == HANDOFF_MISSING
    assert store.resolve_mission(result["mission_id"])["state"] == MissionState.ACTIVE
    store.conn.close()


def test_no_authority_stolen_from_other_planes() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "clankops"
    for name in ("reconciliation.py", "process.py", "attention.py", "resume.py"):
        text = (root / name).read_text(encoding="utf-8").lower()
        for banned in BANNED_AUTHORITY:
            assert banned not in text


def test_non_conflation_laws(tmp_path: Path) -> None:
    store = open_store(tmp_path / "laws.db", actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    store.record_checkpoint(
        mission["display_id"],
        head=MERGE,
        branch="main",
        working_tree="clean",
        source=EventSource.LOCAL_GIT,
    )
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.PAUSED
    packet = resume_packet(store, "oem-radar", include_github=False, now=T0)
    assert packet["admission"]["status"] == "RESUMABLE"
    result = _reconcile(store, mission)
    assert result["observed_at"] != result["evidence_occurred_at"] or result["evidence_occurred_at"] is None
    event = _of(store, EventType.MISSION_STATE_RECONCILED)[0]
    assert event.source != EventSource.GITHUB
    assert event.ts_utc == result["observed_at"]
    assert prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)[
        "admission"
    ]["status"] == "NO_UNFINISHED_MISSION"
    report = attention_report(store, "oem-radar", now=T0, include_github=False)
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.COMPLETED
    assert report["items"] is not None
    store.conn.close()


def test_cli_reconcile_requires_explicit_mission_reason_evidence(
    tmp_path: Path, capsys
) -> None:
    db = str(tmp_path / "cli.db")
    store = open_store(db, actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    store.conn.close()
    with pytest.raises(SystemExit):
        main(
            [
                "--db",
                db,
                "--actor",
                "user",
                "mission",
                "reconcile",
                "--to",
                "COMPLETED",
                "--reason",
                REASON,
                "--evidence",
                "github:pr:6",
                "--basis",
                "github",
            ]
        )
    with pytest.raises(SystemExit):
        main(
            [
                "--db",
                db,
                "--actor",
                "user",
                "mission",
                "reconcile",
                mission["display_id"],
                "--to",
                "COMPLETED",
                "--evidence",
                "github:pr:6",
                "--basis",
                "github",
            ]
        )
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "user",
                "--json",
                "mission",
                "reconcile",
                mission["display_id"],
                "--to",
                "COMPLETED",
                "--reason",
                REASON,
                "--evidence",
                "github:pr:6",
                "--evidence",
                f"github:commit:{MERGE}",
                "--basis",
                "github",
                "--evidence-occurred-at",
                "2026-09-11T12:00:00Z",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["from_state"] == MissionState.PAUSED
    assert payload["to_state"] == MissionState.COMPLETED
    assert payload["session_id"] is None
    assert payload["evidence_occurred_at"].startswith("2026-09-11")
    store = open_store(db, actor="user")
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.COMPLETED
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 1
    store.conn.close()


def test_checkpoint_then_direct_pause_is_not_handoff(tmp_path: Path) -> None:
    store = open_store(tmp_path / "cp-pause.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    session_id = mission["session_id"]
    store.record_checkpoint(mission["display_id"], current_work="pausing directly")
    store.pause_mission(mission["display_id"])
    closed = store.resolve_session(session_id)
    assert derive_handoff_status(store, closed) == HANDOFF_UNKNOWN
    assert _event_count(store, EventType.HANDOFF_RECORDED) == 0
    store.conn.close()


def test_checkpoint_then_direct_block_is_not_handoff(tmp_path: Path) -> None:
    store = open_store(tmp_path / "cp-block.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    session_id = mission["session_id"]
    store.record_checkpoint(mission["display_id"], current_work="blocking directly")
    store.block_mission(mission["display_id"])
    closed = store.resolve_session(session_id)
    assert derive_handoff_status(store, closed) == HANDOFF_UNKNOWN
    assert _event_count(store, EventType.HANDOFF_RECORDED) == 0
    store.conn.close()


def test_ordinary_complete_with_checkpoint_is_not_handoff(tmp_path: Path) -> None:
    store = open_store(tmp_path / "cp-complete.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    session_id = mission["session_id"]
    store.record_checkpoint(mission["display_id"], completed="ordinary complete")
    store.complete_mission(mission["display_id"])
    closed = store.resolve_session(session_id)
    assert derive_handoff_status(store, closed) == HANDOFF_UNKNOWN
    assert _event_count(store, EventType.HANDOFF_RECORDED) == 0
    store.conn.close()


def test_reconciliation_rejects_evidence_plane_source_before_write(
    tmp_path: Path, capsys
) -> None:
    db = str(tmp_path / "src.db")
    store = open_store(db, actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    before = ledger_fingerprint(store)
    before_types = _types(store)
    for plane in (
        EventSource.GITHUB,
        EventSource.CI,
        EventSource.DEPLOYMENT,
        EventSource.LOCAL_GIT,
        EventSource.AGENT_REPORT,
    ):
        with pytest.raises(ValidationError, match="must be USER"):
            _reconcile(store, mission, source=plane)
        assert ledger_fingerprint(store) == before
        assert _types(store) == before_types
        assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 0
    store.conn.close()

    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "user",
                "--source",
                "GITHUB",
                "mission",
                "reconcile",
                mission["display_id"],
                "--to",
                "COMPLETED",
                "--reason",
                REASON,
                "--evidence",
                "github:pr:6",
                "--basis",
                "github",
            ]
        )
        == 2
    )
    err = capsys.readouterr().err
    assert "must be USER" in err
    store = open_store(db, actor="user")
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.PAUSED
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 0
    store.conn.close()


def test_reconciliation_from_state_is_locked_observation(tmp_path: Path) -> None:
    store = open_store(tmp_path / "from.db", actor="user", clock=FrozenClock(T0))
    mission = _paused(store)
    result = _reconcile(store, mission)
    event = _of(store, EventType.MISSION_STATE_RECONCILED)[0]
    assert event.payload["from_state"] == MissionState.PAUSED
    assert result["from_state"] == MissionState.PAUSED
    assert event.source == EventSource.USER
    assert {item["source"] for item in event.payload["evidence"]} == {"GITHUB"}
    store.conn.close()


def test_reconciliation_fails_if_mission_became_active(tmp_path: Path) -> None:
    db = tmp_path / "became-active.db"
    setup = open_store(db, actor="user", clock=FrozenClock(T0))
    mission = _paused(setup)
    setup.resume_mission(mission["display_id"])
    setup.conn.close()
    store = open_store(db, actor="user", clock=FrozenClock(T0))
    before = _event_count(store, EventType.MISSION_STATE_RECONCILED)
    with pytest.raises(InvalidTransitionError):
        _reconcile(store, mission)
    assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == before
    row = store.resolve_mission(mission["display_id"])
    assert row["state"] == MissionState.ACTIVE
    open_ids = [
        item["session_id"]
        for item in store.list_sessions(mission["display_id"])
        if item["ended_utc"] is None
    ]
    assert open_ids
    store.conn.close()


def test_reconciliation_vs_resume_one_winner_never_completed_open(
    tmp_path: Path,
) -> None:
    db = tmp_path / "race.db"
    setup = open_store(db, actor="user", clock=FrozenClock(T0))
    mission = _paused(setup)
    display = mission["display_id"]
    setup.conn.close()
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()

    def resume() -> None:
        store = open_store(db, actor="cursor")
        try:
            barrier.wait(timeout=10)
            store.resume_mission(display)
            with lock:
                outcomes.append(("resume", "ok"))
        except ClankOpsError:
            with lock:
                outcomes.append(("resume", "err"))
        finally:
            store.conn.close()

    def reconcile() -> None:
        store = open_store(db, actor="user")
        try:
            barrier.wait(timeout=10)
            _reconcile(store, display)
            with lock:
                outcomes.append(("reconcile", "ok"))
        except ClankOpsError:
            with lock:
                outcomes.append(("reconcile", "err"))
        finally:
            store.conn.close()

    threads = [threading.Thread(target=resume), threading.Thread(target=reconcile)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    wins = [row for row in outcomes if row[1] == "ok"]
    losses = [row for row in outcomes if row[1] == "err"]
    assert len(wins) == 1
    assert len(losses) == 1
    store = open_store(db, actor="user")
    row = store.resolve_mission(display)
    open_ids = [
        item["session_id"]
        for item in store.list_sessions(display)
        if item["ended_utc"] is None
    ]
    if row["state"] == MissionState.COMPLETED:
        assert open_ids == []
        assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 1
        assert ("reconcile", "ok") in outcomes
    else:
        assert row["state"] == MissionState.ACTIVE
        assert open_ids
        assert _event_count(store, EventType.MISSION_STATE_RECONCILED) == 0
        assert ("resume", "ok") in outcomes
    store.conn.close()


def test_unrelated_mission_writes_do_not_corrupt_reconciliation(tmp_path: Path) -> None:
    store = open_store(tmp_path / "attr.db", actor="user", clock=FrozenClock(T0))
    first = _paused(store)
    second = store.start_mission("oem-radar", "unrelated live work")
    store.record_checkpoint(second["display_id"], current_work="other mission")
    result = _reconcile(store, first)
    event = _of(store, EventType.MISSION_STATE_RECONCILED)[0]
    assert event.mission_id == first["mission_id"]
    assert event.payload["mission_id"] == first["mission_id"]
    assert event.payload["from_state"] == MissionState.PAUSED
    assert result["from_state"] == MissionState.PAUSED
    assert store.resolve_mission(second["display_id"])["state"] == MissionState.ACTIVE
    assert store.resolve_mission(first["display_id"])["state"] == MissionState.COMPLETED
    store.conn.close()
