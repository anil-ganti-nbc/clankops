"""Foundation 10: managed agent process-exit observability."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from clankops.agent import admit_agent
from clankops.attention import (
    REASON_CI_EVIDENCE_BEHIND_MISSION,
    REASON_DEPLOYMENT_DIFFERS_FROM_MISSION,
    REASON_DIRTY_WITHOUT_OPEN_SESSION,
    REASON_GIT_DRIFT,
    REASON_MANAGED_PROCESS_EXITED_WITH_OPEN_SESSION,
    REASON_MISSION_NO_NEXT_ACTION,
    REASON_STALE_OPEN_SESSION,
    attention_report,
)
from clankops.clock import FrozenClock
from clankops.enums import EventType, MissionState
from clankops.errors import ValidationError
from clankops.events import list_events
from clankops.launch import launch_agent
from clankops.process import (
    FORBIDDEN_PAYLOAD_KEYS,
    KIND_EXITED,
    KIND_START_FAILED,
    STATUS_UNKNOWN,
    argument_is_secret_shaped,
    command_identity,
    latest_observation,
    observation_facts_for_clank,
    observations_for_session,
    record_process_exited,
)
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.readmodel import dossier, ledger_fingerprint, open_sessions
from clankops.resume import resume_packet
from clankops.store import open_store
from clankops.terminal import _dossier_html

from test_foundation2 import T0
from test_foundation3 import _seed
from test_foundation9 import FakeProc, _event_count, _paused

FOUNDATION_7_REASON_CODES = {
    REASON_MISSION_NO_NEXT_ACTION,
    REASON_STALE_OPEN_SESSION,
    REASON_GIT_DRIFT,
    REASON_DIRTY_WITHOUT_OPEN_SESSION,
    REASON_CI_EVIDENCE_BEHIND_MISSION,
    REASON_DEPLOYMENT_DIFFERS_FROM_MISSION,
}
SECRET_TOKEN = "ghp_foundation10notarealtokenvalue"
WEBHOOK = "https://hooks.slack.com/services/T00/B00/secret"
SECRET_ENV = "super-secret-env-value-foundation10"
MISSING_EXE = "definitely-not-an-executable-clankops-f10"


def _codes(report: dict) -> list[str]:
    return [item["reason_code"] for item in report["items"]]


def _of(report: dict, code: str) -> list[dict]:
    return [item for item in report["items"] if item["reason_code"] == code]


def _events_blob(store) -> str:
    parts = []
    for event in list_events(store.conn):
        parts.append(json.dumps(event.payload, sort_keys=True, default=str))
        parts.append(json.dumps(event.provenance, sort_keys=True, default=str))
        parts.append(event.event_type)
    return "\n".join(parts)


def _process_events(store):
    return [
        event
        for event in list_events(store.conn)
        if event.event_type
        in {EventType.AGENT_PROCESS_EXITED, EventType.AGENT_PROCESS_START_FAILED}
    ]


def _launch(
    store,
    *,
    actor: str = "cursor",
    launcher: str | None = None,
    returncode: int = 0,
    command: list[str] | None = None,
    runner=None,
    environ: dict | None = None,
    clank: str = "oem-radar",
):
    if runner is None and command is None:
        runner = FakeProc(returncode=returncode)
        command = [sys.executable, "-c", "pass"]
    return launch_agent(
        store,
        clank,
        actor=actor,
        launcher=launcher or actor,
        command=command if command is not None else [sys.executable, "-c", "pass"],
        include_github=False,
        now=T0,
        environ={} if environ is None and runner is not None else environ,
        runner=runner,
    )


def test_command_identity_never_returns_argv_or_shell() -> None:
    identity = command_identity(
        [r"C:\Python\python.exe", "--api-key=" + SECRET_TOKEN, WEBHOOK, "x" * 250]
    )
    assert identity["executable"] == "python.exe"
    assert identity["argv_count"] == 4
    assert identity["argv_redacted"] is True
    assert "argv" not in identity
    assert "command" not in identity
    dumped = json.dumps(identity)
    assert SECRET_TOKEN not in dumped
    assert "hooks.slack.com" not in dumped
    assert argument_is_secret_shaped(SECRET_TOKEN) is True
    assert argument_is_secret_shaped("pass") is False


def test_successful_managed_launch_records_one_exit_for_that_session(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "ok.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    result = _launch(store, returncode=0)
    events = [
        event
        for event in list_events(store.conn)
        if event.event_type == EventType.AGENT_PROCESS_EXITED
    ]
    assert len(events) == 1
    event = events[0]
    assert event.session_id == result["session_id"]
    assert event.mission_id == result["mission_id"]
    assert event.clank_id == mission["clank_id"]
    assert event.payload["session_id"] == result["session_id"]
    assert event.payload["exit_code"] == 0
    assert event.payload["kind"] == KIND_EXITED
    assert event.payload["actor"] == "cursor"
    assert event.payload["launcher"] == "cursor"
    assert event.payload["context_fingerprint"] == result["context_fingerprint"]
    assert event.payload["observed_at"]
    assert event.payload["source"] == "SYSTEM"
    session = store.resolve_session(result["session_id"])
    assert session["ended_utc"] is None
    assert store.resolve_mission(result["mission_id"])["state"] == MissionState.ACTIVE
    store.conn.close()


def test_real_child_exit_zero_leaves_session_open(tmp_path: Path) -> None:
    store = open_store(tmp_path / "real0.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = launch_agent(
        store,
        "oem-radar",
        actor="cursor",
        launcher="cursor",
        command=[sys.executable, "-c", "raise SystemExit(0)"],
        include_github=False,
        now=T0,
        environ=None,
        runner=None,
    )
    assert result["exit_code"] == 0
    assert result["process"]["kind"] == KIND_EXITED
    row = store.resolve_session(result["session_id"])
    assert row["ended_utc"] is None
    observed = open_sessions(store, now=T0)
    match = next(item for item in observed if item["session_id"] == result["session_id"])
    assert match["managed_process"]["status"] == KIND_EXITED
    assert match["managed_process"]["session"] == "OPEN"
    assert match["managed_process"]["handoff"] == "MISSING"
    assert match["open"] is True
    store.conn.close()


def test_nonzero_exit_is_evidence_not_mission_failure(tmp_path: Path) -> None:
    store = open_store(tmp_path / "nz.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    checkpoints = _event_count(store, EventType.CHECKPOINT_RECORDED)
    ended = _event_count(store, EventType.SESSION_ENDED)
    result = _launch(store, returncode=7)
    assert result["exit_code"] == 7
    event = _process_events(store)[0]
    assert event.event_type == EventType.AGENT_PROCESS_EXITED
    assert event.payload["exit_code"] == 7
    assert store.resolve_session(result["session_id"])["ended_utc"] is None
    after = store.resolve_mission(mission["mission_id"])
    assert after["state"] == MissionState.ACTIVE
    assert _event_count(store, EventType.CHECKPOINT_RECORDED) == checkpoints
    assert _event_count(store, EventType.SESSION_ENDED) == ended
    assert "next_action" not in json.dumps(event.payload)
    store.conn.close()


def test_neither_exit_path_creates_checkpoint_handoff_or_next_action(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "nohand.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    before_types = {event.event_type for event in list_events(store.conn)}
    _launch(store, returncode=0)
    _launch(
        store,
        actor="grok",
        launcher="grok",
        returncode=3,
    )
    new_types = {event.event_type for event in list_events(store.conn)} - before_types
    assert EventType.AGENT_PROCESS_EXITED in new_types
    assert EventType.CHECKPOINT_RECORDED not in new_types
    assert EventType.SESSION_ENDED not in new_types
    for event in _process_events(store):
        assert "next_action" not in event.payload
        assert "completed" not in event.payload
    store.conn.close()


def test_attention_appears_while_session_open_and_clears_after_end(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "attn.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = _launch(store, returncode=0)
    report = attention_report(store, "oem-radar", now=T0, include_github=False)
    items = _of(report, REASON_MANAGED_PROCESS_EXITED_WITH_OPEN_SESSION)
    assert len(items) == 1
    item = items[0]
    assert item["mission"] == result["mission"]
    assert item["evidence"]["session_id"] == result["session_id"]
    assert item["evidence"]["actor"] == "cursor"
    assert item["evidence"]["launcher"] == "cursor"
    assert item["evidence"]["exit_code"] == 0
    assert item["class"] == "informational"
    assert "handoff" in (item.get("suggested_action") or "").lower()
    before = ledger_fingerprint(store)
    attention_report(store, "oem-radar", now=T0, include_github=False)
    assert ledger_fingerprint(store) == before
    store.end_session(result["session_id"])
    closed = attention_report(store, "oem-radar", now=T0, include_github=False)
    assert _of(closed, REASON_MANAGED_PROCESS_EXITED_WITH_OPEN_SESSION) == []
    assert store.resolve_session(result["session_id"])["ended_utc"]
    assert store.resolve_mission(result["mission_id"])["state"] == MissionState.ACTIVE
    store.conn.close()


def test_process_exit_on_session_a_cannot_affect_session_b(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ab.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    a = _launch(store, actor="cursor", returncode=0)
    b = _launch(store, actor="grok", launcher="grok", returncode=4)
    obs_a = observations_for_session(store, a["session_id"])
    obs_b = observations_for_session(store, b["session_id"])
    assert len(obs_a) == 1
    assert len(obs_b) == 1
    assert obs_a[0]["session_id"] == a["session_id"]
    assert obs_b[0]["session_id"] == b["session_id"]
    assert obs_a[0]["exit_code"] == 0
    assert obs_b[0]["exit_code"] == 4
    assert obs_a[0]["clank_id"] == obs_b[0]["clank_id"]
    assert obs_a[0]["mission_id"] == a["mission_id"]
    view_a = next(
        row for row in open_sessions(store, now=T0) if row["session_id"] == a["session_id"]
    )
    view_b = next(
        row for row in open_sessions(store, now=T0) if row["session_id"] == b["session_id"]
    )
    assert view_a["managed_process"]["exit_code"] == 0
    assert view_b["managed_process"]["exit_code"] == 4
    store.conn.close()


def test_unrelated_fleet_exit_does_not_change_attribution(tmp_path: Path) -> None:
    store = open_store(tmp_path / "fleet.db", actor="cursor", clock=FrozenClock(T0))
    oem = _paused(store)
    first = _launch(store, returncode=0)
    before = [dict(row) for row in observations_for_session(store, first["session_id"])]
    store.register_clank("watch-clank", display_name="Watch Clank")
    other = store.start_mission("watch-clank", "other work")
    store.pause_mission(other["display_id"])
    second = _launch(store, clank="watch-clank", actor="codex", launcher="codex", returncode=2)
    after = [dict(row) for row in observations_for_session(store, first["session_id"])]
    assert after == before
    oem_facts = observation_facts_for_clank(store, oem["clank_id"])
    assert {row["session_id"] for row in oem_facts} == {first["session_id"]}
    assert second["session_id"] not in {row["session_id"] for row in oem_facts}
    store.conn.close()


def test_inherited_environment_and_secret_argv_are_not_persisted(tmp_path: Path) -> None:
    store = open_store(tmp_path / "secret.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    runner = FakeProc(returncode=0)
    command = [
        sys.executable,
        "--token=" + SECRET_TOKEN,
        WEBHOOK,
        "paste-this-source\n" + ("def secrets():\n    return 1\n" * 20),
    ]
    result = launch_agent(
        store,
        "oem-radar",
        actor="cursor",
        launcher="cursor",
        command=command,
        include_github=False,
        now=T0,
        environ={"PATH": "C:\\secret-home-path", "API_KEY": SECRET_ENV, "HOME": "C:\\secret-home-path"},
        runner=runner,
    )
    blob = _events_blob(store)
    assert SECRET_TOKEN not in blob
    assert SECRET_ENV not in blob
    assert "hooks.slack.com" not in blob
    assert "C:\\secret-home-path" not in blob
    assert "def secrets" not in blob
    event = _process_events(store)[0]
    assert FORBIDDEN_PAYLOAD_KEYS.isdisjoint(event.payload)
    assert event.payload["argv_redacted"] is True
    assert event.payload["argv_count"] == 4
    assert "argv" not in event.payload
    identity = command_identity(command)
    assert identity["argv_redacted"] is True
    assert result["process"]["kind"] == KIND_EXITED
    store.conn.close()


def test_admission_failure_does_not_record_process_events(tmp_path: Path) -> None:
    store = open_store(tmp_path / "admit.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    before = ledger_fingerprint(store)
    runner = FakeProc()
    with pytest.raises(ValidationError, match="command is required"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=[],
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    assert runner.calls == []
    assert ledger_fingerprint(store) == before
    assert _process_events(store) == []
    store.conn.close()


def test_start_failure_is_distinct_and_not_exited(tmp_path: Path) -> None:
    store = open_store(tmp_path / "fail.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    with pytest.raises(ValidationError, match="could not be started"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=[MISSING_EXE],
            include_github=False,
            now=T0,
            environ={},
            runner=None,
        )
    failed = [
        event
        for event in list_events(store.conn)
        if event.event_type == EventType.AGENT_PROCESS_START_FAILED
    ]
    exited = [
        event
        for event in list_events(store.conn)
        if event.event_type == EventType.AGENT_PROCESS_EXITED
    ]
    assert len(failed) == 1
    assert exited == []
    payload = failed[0].payload
    assert payload["kind"] == KIND_START_FAILED
    assert payload["error"] == "FileNotFoundError"
    assert "exit_code" not in payload or payload.get("exit_code") is None
    opened = open_sessions(store, now=T0)
    assert len(opened) == 1
    assert opened[0]["managed_process"]["status"] == KIND_START_FAILED
    assert opened[0]["open"] is True
    assert store.resolve_mission(mission["mission_id"])["state"] == MissionState.ACTIVE
    report = attention_report(store, "oem-radar", now=T0, include_github=False)
    assert _of(report, REASON_MANAGED_PROCESS_EXITED_WITH_OPEN_SESSION) == []
    store.conn.close()


def test_start_failure_does_not_persist_exception_secret(tmp_path: Path) -> None:
    store = open_store(tmp_path / "failsec.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)

    def boom(*_args, **_kwargs):
        raise FileNotFoundError(SECRET_TOKEN)

    with pytest.raises(ValidationError, match="could not be started"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=[sys.executable, SECRET_TOKEN],
            include_github=False,
            now=T0,
            environ={},
            runner=boom,
        )
    blob = _events_blob(store)
    assert SECRET_TOKEN not in blob
    event = _process_events(store)[0]
    assert event.event_type == EventType.AGENT_PROCESS_START_FAILED
    assert event.payload["error"] == "FileNotFoundError"
    store.conn.close()


def test_missing_process_evidence_is_unknown_never_running(tmp_path: Path) -> None:
    store = open_store(tmp_path / "unk.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    admitted = admit_agent(
        store,
        "oem-radar",
        mission["display_id"],
        actor="cursor",
        include_github=False,
        now=T0,
    )
    assert _process_events(store) == []
    row = next(
        item
        for item in open_sessions(store, now=T0)
        if item["session_id"] == admitted["session_id"]
    )
    assert row["managed_process"]["status"] == STATUS_UNKNOWN
    assert row["managed_process"]["status"] != "RUNNING"
    assert "RUNNING" not in json.dumps(row["managed_process"])
    packet = resume_packet(store, "oem-radar", now=T0, include_github=False)
    dumped = json.dumps(packet)
    assert "RUNNING" not in dumped
    store.conn.close()


def test_resume_packet_age_does_not_churn_fingerprint_but_new_evidence_does(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "fp.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = _launch(store, returncode=0)
    first = resume_packet(store, "oem-radar", now=T0, include_github=False)
    later = resume_packet(
        store, "oem-radar", now=T0 + timedelta(hours=2), include_github=False
    )
    assert first["context_fingerprint"] == later["context_fingerprint"]
    sessions = first["open_sessions"]
    assert sessions[0]["managed_process"]["status"] == KIND_EXITED
    assert sessions[0]["managed_process"]["exit_code"] == 0
    assert first["managed_process_observations"]
    assert first["managed_process_observations"][0]["session_id"] == result["session_id"]
    record_process_exited(
        store,
        session_id=result["session_id"],
        exit_code=0,
        argv=[sys.executable, "-c", "pass"],
        actor="cursor",
        launcher="cursor",
        context_fingerprint=result["context_fingerprint"],
    )
    changed = resume_packet(store, "oem-radar", now=T0, include_github=False)
    assert changed["context_fingerprint"] != first["context_fingerprint"]
    assert len(changed["managed_process_observations"]) == 2
    store.conn.close()


def test_projection_rebuild_is_identical(tmp_path: Path) -> None:
    store = open_store(tmp_path / "rebuild.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = _launch(store, returncode=1)
    before = dump_projection_state(store.conn)
    rebuild_projections(store.conn)
    after = dump_projection_state(store.conn)
    assert before == after
    latest = latest_observation(store, result["session_id"])
    assert latest["kind"] == KIND_EXITED
    assert latest["exit_code"] == 1
    store.conn.close()


def test_raw_admit_still_reuses_and_does_not_emit_process_events(
    tmp_path: Path,
) -> None:
    store = open_store(tmp_path / "raw.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    existing = mission["session_id"]
    before = ledger_fingerprint(store)
    result = admit_agent(
        store,
        "oem-radar",
        mission["display_id"],
        actor="cursor",
        include_github=False,
        now=T0,
        launcher="cursor",
    )
    assert result["session_id"] == existing
    assert ledger_fingerprint(store) == before
    assert _process_events(store) == []
    row = store.resolve_session(existing)
    assert row["ended_utc"] is None
    assert row["launcher"] is None
    store.conn.close()


def test_foundation7_reason_codes_remain_unchanged(tmp_path: Path) -> None:
    assert FOUNDATION_7_REASON_CODES == {
        "MISSION_NO_NEXT_ACTION",
        "STALE_OPEN_SESSION",
        "GIT_DRIFT",
        "DIRTY_WITHOUT_OPEN_SESSION",
        "CI_EVIDENCE_BEHIND_MISSION",
        "DEPLOYMENT_DIFFERS_FROM_MISSION",
    }
    store = open_store(tmp_path / "f7.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("watch-clank", display_name="Watch Clank")
    store.start_mission("watch-clank", "keep collecting")
    report = attention_report(store, "watch-clank", now=T0, include_github=False)
    assert REASON_MISSION_NO_NEXT_ACTION in _codes(report)
    assert REASON_MANAGED_PROCESS_EXITED_WITH_OPEN_SESSION not in _codes(report)
    store.conn.close()


def test_terminal_dossier_exposes_process_without_colour_only(tmp_path: Path) -> None:
    store = open_store(tmp_path / "term.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = _launch(store, returncode=0)
    payload = dossier(store, "oem-radar", now=T0, include_github=False)
    html = _dossier_html(payload)
    assert "[SESSION]" in html
    assert "[EXITED]" in html
    assert "code 0" in html
    assert "[OPEN SESSION]" in html
    assert "explicit handoff required" in html
    assert result["session_id"] in html
    store.conn.close()


def test_history_preserves_multiple_observations_on_one_session(tmp_path: Path) -> None:
    store = open_store(tmp_path / "hist.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    result = _launch(store, returncode=0)
    record_process_exited(
        store,
        session_id=result["session_id"],
        exit_code=9,
        argv=["python", "-c", "pass"],
        actor="cursor",
        launcher="cursor",
        context_fingerprint=result["context_fingerprint"],
    )
    rows = observations_for_session(store, result["session_id"])
    assert len(rows) == 2
    assert rows[0]["exit_code"] == 0
    assert rows[1]["exit_code"] == 9
    assert rows[0]["observation_id"] != rows[1]["observation_id"]
    assert rows[0]["ledger_seq"] < rows[1]["ledger_seq"]
    store.conn.close()
