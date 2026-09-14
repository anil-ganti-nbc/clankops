"""Foundation 9: managed agent launch gate and Session launcher provenance."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from clankops.agent import admit_agent, prepare_agent
from clankops.cli import main
from clankops.clock import FrozenClock
from clankops.enums import EventType, MissionState
from clankops.errors import ValidationError
from clankops.events import list_events
from clankops.launch import launch_agent
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.readmodel import ledger_fingerprint, open_sessions
from clankops.resume import STATUS_AMBIGUOUS, STATUS_NO_UNFINISHED_MISSION, STATUS_RESUMABLE
from clankops.store import Store, open_store

from test_foundation2 import T0
from test_foundation3 import _seed
from test_foundation8 import _open_session_ids, _pkt

ROOT = Path(__file__).resolve().parents[1]
AGENT_WRAPPERS = [
    ROOT / "examples" / "agents" / "cursor.ps1",
    ROOT / "examples" / "agents" / "codex.ps1",
    ROOT / "examples" / "agents" / "glm.ps1",
    ROOT / "examples" / "agents" / "grok.ps1",
]
DEV_WRAPPER = ROOT / "scripts" / "clankops-dev.ps1"
SECRET_RE = re.compile(r"(?i)(api[_-]?key|\bsecret\b|\bpassword\b|\bghp_|\bsk-)")
ACTORS = ("cursor", "codex", "glm", "grok")


class FakeProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        shell = kwargs.get("shell", False)
        self.calls.append(
            {
                "argv": list(argv),
                "env": dict(kwargs.get("env") or {}),
                "shell": shell,
                "cwd": kwargs.get("cwd"),
            }
        )
        if shell:
            raise AssertionError("managed launch must not use shell=True")
        return subprocess.CompletedProcess(list(argv), self.returncode)


def _paused(store):
    mission = _seed(store)
    store.pause_mission(mission["display_id"])
    return mission


def _event_count(store, event_type: str) -> int:
    row = store.conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = ?",
        (event_type,),
    ).fetchone()
    return int(row["n"])


def _session_started(store):
    return [event for event in list_events(store.conn) if event.event_type == EventType.SESSION_STARTED]


def test_prepare_via_launch_path_is_still_zero_write(tmp_path: Path) -> None:
    store = open_store(tmp_path / "prep.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    before = ledger_fingerprint(store)
    packet = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    assert packet["wrote_events"] is False
    assert packet["admission"]["status"] == STATUS_RESUMABLE
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_launcher_refuses_ambiguous_without_explicit_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "amb.db", actor="cursor", clock=FrozenClock(T0))
    first = _seed(store)
    store.pause_mission(first["display_id"])
    store.start_mission("oem-radar", "second objective")
    before = ledger_fingerprint(store)
    runner = FakeProc()
    with pytest.raises(ValidationError, match="AMBIGUOUS"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=[sys.executable, "-c", "pass"],
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    assert runner.calls == []
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_launcher_refuses_no_unfinished_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "none.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    store.complete_mission(mission["display_id"])
    before = ledger_fingerprint(store)
    runner = FakeProc()
    with pytest.raises(ValidationError, match="NO_UNFINISHED_MISSION"):
        launch_agent(
            store,
            "oem-radar",
            actor="glm",
            launcher="glm",
            command=[sys.executable, "-c", "pass"],
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    assert runner.calls == []
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_stale_context_prevents_agent_process_invocation(tmp_path: Path) -> None:
    store = open_store(tmp_path / "stale.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    packet = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    stale = packet["context_fingerprint"]
    store.record_checkpoint(mission["display_id"], next_action="changed after prepare")
    before = ledger_fingerprint(store)
    sessions_before = _open_session_ids(store, mission["clank_id"])
    runner = FakeProc()
    with pytest.raises(ValidationError, match="stale context"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=[sys.executable, "-c", "pass"],
            expect_context=stale,
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    assert runner.calls == []
    assert ledger_fingerprint(store) == before
    assert _open_session_ids(store, mission["clank_id"]) == sessions_before
    store.conn.close()


@pytest.mark.parametrize("actor", ACTORS)
def test_valid_admission_creates_exactly_one_session_then_invokes(tmp_path: Path, actor: str) -> None:
    store = open_store(tmp_path / f"ok-{actor}.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    runner = FakeProc()
    started_before = len(_session_started(store))
    result = launch_agent(
        store,
        "oem-radar",
        actor=actor,
        launcher=actor,
        command=[sys.executable, "-c", "pass"],
        include_github=False,
        now=T0,
        environ={"PATH": "C:\\Windows"},
        runner=runner,
    )
    assert result["launched"] is True
    assert result["session_id"]
    assert len(runner.calls) == 1
    assert runner.calls[0]["argv"] == [sys.executable, "-c", "pass"]
    assert runner.calls[0]["shell"] is False
    opened = _open_session_ids(store, mission["clank_id"])
    assert opened == [result["session_id"]]
    assert len(_session_started(store)) == started_before + 1
    child = runner.calls[0]["env"]
    assert child["CLANKOPS_CLANK"] == "oem-radar"
    assert child["CLANKOPS_MISSION"] == mission["display_id"]
    assert child["CLANKOPS_MISSION_ID"] == mission["mission_id"]
    assert child["CLANKOPS_SESSION_ID"] == result["session_id"]
    assert child["CLANKOPS_CONTEXT_FINGERPRINT"] == result["context_fingerprint"]
    assert child["CLANKOPS_ACTOR"] == actor
    assert child["CLANKOPS_LAUNCHER"] == actor
    row = store.resolve_session(result["session_id"])
    assert row["launcher"] == actor
    assert row["launcher"] == child["CLANKOPS_LAUNCHER"]
    assert row["context_fingerprint"] == result["context_fingerprint"]
    assert row["context_fingerprint"] == child["CLANKOPS_CONTEXT_FINGERPRINT"]
    assert row["source"]
    started = [event for event in _session_started(store) if event.session_id == result["session_id"]]
    assert len(started) == 1
    assert started[0].payload.get("launcher") == actor
    assert started[0].payload.get("context_fingerprint") == result["context_fingerprint"]
    store.conn.close()


def test_shell_arguments_cannot_change_invoked_command(tmp_path: Path) -> None:
    store = open_store(tmp_path / "argv.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    argv_file = tmp_path / "argv.json"
    command = [
        sys.executable,
        "-c",
        "import json,sys; json.dump(sys.argv[1:], open(sys.argv[1], 'w'))",
        str(argv_file),
        "hello world",
        'a "quoted" token',
    ]
    result = launch_agent(
        store,
        "oem-radar",
        actor="cursor",
        launcher="cursor",
        command=command,
        include_github=False,
        now=T0,
        environ=os.environ,
    )
    assert result["exit_code"] == 0
    assert json.loads(argv_file.read_text(encoding="utf-8")) == [
        str(argv_file),
        "hello world",
        'a "quoted" token',
    ]
    store.conn.close()


def test_child_receives_identifiers_in_real_process(tmp_path: Path) -> None:
    store = open_store(tmp_path / "env.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    env_file = tmp_path / "child-env.json"
    keys = (
        "CLANKOPS_CLANK",
        "CLANKOPS_MISSION",
        "CLANKOPS_MISSION_ID",
        "CLANKOPS_SESSION_ID",
        "CLANKOPS_CONTEXT_FINGERPRINT",
        "CLANKOPS_ACTOR",
        "CLANKOPS_LAUNCHER",
    )
    command = [
        sys.executable,
        "-c",
        "import json,os,sys; json.dump({k: os.environ.get(k) for k in sys.argv[1:]}, open(sys.argv[1], 'w'))",
        str(env_file),
        *keys,
    ]
    result = launch_agent(
        store,
        "oem-radar",
        actor="grok",
        launcher="grok",
        command=command,
        include_github=False,
        now=T0,
        environ=os.environ,
    )
    dumped = json.loads(env_file.read_text(encoding="utf-8"))
    assert dumped["CLANKOPS_CLANK"] == "oem-radar"
    assert dumped["CLANKOPS_MISSION"] == mission["display_id"]
    assert dumped["CLANKOPS_MISSION_ID"] == mission["mission_id"]
    assert dumped["CLANKOPS_SESSION_ID"] == result["session_id"]
    assert dumped["CLANKOPS_CONTEXT_FINGERPRINT"] == result["context_fingerprint"]
    assert dumped["CLANKOPS_ACTOR"] == "grok"
    assert dumped["CLANKOPS_LAUNCHER"] == "grok"
    store.conn.close()


def test_launcher_identity_survives_event_provenance_and_rebuild(tmp_path: Path) -> None:
    store = open_store(tmp_path / "prov.db", actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    runner = FakeProc()
    result = launch_agent(
        store,
        "oem-radar",
        actor="codex",
        launcher="codex",
        command=["tool", "run"],
        include_github=False,
        now=T0,
        environ={},
        runner=runner,
        source="AGENT_REPORT",
    )
    started = [event for event in _session_started(store) if event.session_id == result["session_id"]]
    assert len(started) == 1
    event = started[0]
    assert event.payload.get("launcher") == "codex"
    assert event.provenance.get("launcher") == "codex"
    assert event.payload.get("context_fingerprint") == result["context_fingerprint"]
    assert event.source == "AGENT_REPORT"
    before = dict(store.resolve_session(result["session_id"]))
    rebuild_projections(store.conn)
    after = dict(store.resolve_session(result["session_id"]))
    assert after["launcher"] == "codex"
    assert after["context_fingerprint"] == result["context_fingerprint"]
    assert after["source"] == "AGENT_REPORT"
    assert after["actor"] == "codex"
    assert after["started_utc"] == before["started_utc"]
    observed = open_sessions(store, now=T0)
    match = next(row for row in observed if row["session_id"] == result["session_id"])
    assert match["launcher"] == "codex"
    assert match["context_fingerprint"] == result["context_fingerprint"]
    assert match["source"] == "AGENT_REPORT"
    store.conn.close()


def test_child_exit_does_not_complete_mission_or_fabricate_checkpoint(tmp_path: Path) -> None:
    store = open_store(tmp_path / "exit.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    checkpoints_before = _event_count(store, EventType.CHECKPOINT_RECORDED)
    runner = FakeProc(returncode=7)
    result = launch_agent(
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
    assert result["exit_code"] == 7
    row = store.resolve_mission(mission["mission_id"])
    assert row["state"] == MissionState.ACTIVE
    assert _event_count(store, EventType.CHECKPOINT_RECORDED) == checkpoints_before
    assert _event_count(store, EventType.MISSION_STATE_CHANGED) >= 1
    store.conn.close()


def test_missing_command_or_actor_or_launcher_fails_before_spawn(tmp_path: Path) -> None:
    store = open_store(tmp_path / "req.db", actor="cursor", clock=FrozenClock(T0))
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
    with pytest.raises(ValidationError, match="actor is required"):
        launch_agent(
            store,
            "oem-radar",
            actor="  ",
            launcher="cursor",
            command=["agent"],
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    with pytest.raises(ValidationError, match="launcher identity is required"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="",
            command=["agent"],
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    assert runner.calls == []
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_active_mission_without_same_actor_session_creates_fresh_session(tmp_path: Path) -> None:
    store = open_store(tmp_path / "active-other.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    cursor_session = mission["session_id"]
    started_before = len(_session_started(store))
    runner = FakeProc()
    result = launch_agent(
        store,
        "oem-radar",
        actor="glm",
        launcher="glm",
        command=["agent"],
        include_github=False,
        now=T0,
        environ={},
        runner=runner,
    )
    assert result["launched"] is True
    assert result["session_id"] != cursor_session
    assert len(runner.calls) == 1
    assert len(_session_started(store)) == started_before + 1
    child = runner.calls[0]["env"]
    row = store.resolve_session(result["session_id"])
    assert row["launcher"] == "glm"
    assert row["launcher"] == child["CLANKOPS_LAUNCHER"]
    assert row["context_fingerprint"] == result["context_fingerprint"]
    assert row["context_fingerprint"] == child["CLANKOPS_CONTEXT_FINGERPRINT"]
    assert set(_open_session_ids(store, mission["clank_id"])) == {
        cursor_session,
        result["session_id"],
    }
    cursor = store.resolve_session(cursor_session)
    assert cursor["ended_utc"] is None
    assert cursor["launcher"] is None
    rebuild_projections(store.conn)
    after = store.resolve_session(result["session_id"])
    assert after["launcher"] == child["CLANKOPS_LAUNCHER"]
    assert after["context_fingerprint"] == child["CLANKOPS_CONTEXT_FINGERPRINT"]
    store.conn.close()


def test_active_same_actor_open_session_refuses_before_spawn(tmp_path: Path) -> None:
    store = open_store(tmp_path / "reuse.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    existing = mission["session_id"]
    before_fp = ledger_fingerprint(store)
    before_proj = dump_projection_state(store.conn)
    before_row = dict(store.resolve_session(existing))
    started_before = len(_session_started(store))
    checkpoints_before = _event_count(store, EventType.CHECKPOINT_RECORDED)
    ended_before = _event_count(store, EventType.SESSION_ENDED)
    transitions_before = _event_count(store, EventType.MISSION_STATE_CHANGED)
    runner = FakeProc()
    with pytest.raises(ValidationError, match="open Session already exists"):
        launch_agent(
            store,
            "oem-radar",
            actor="cursor",
            launcher="cursor",
            command=[sys.executable, "-c", "pass"],
            include_github=False,
            now=T0,
            environ={},
            runner=runner,
        )
    assert runner.calls == []
    assert ledger_fingerprint(store) == before_fp
    assert dump_projection_state(store.conn) == before_proj
    after_row = dict(store.resolve_session(existing))
    assert after_row == before_row
    assert after_row["ended_utc"] is None
    assert after_row["launcher"] is None
    assert after_row["context_fingerprint"] is None
    assert _open_session_ids(store, mission["clank_id"]) == [existing]
    assert store.resolve_mission(mission["mission_id"])["state"] == MissionState.ACTIVE
    assert len(_session_started(store)) == started_before
    assert _event_count(store, EventType.CHECKPOINT_RECORDED) == checkpoints_before
    assert _event_count(store, EventType.SESSION_ENDED) == ended_before
    assert _event_count(store, EventType.MISSION_STATE_CHANGED) == transitions_before
    store.conn.close()


def test_raw_admit_still_reuses_open_same_actor_session(tmp_path: Path) -> None:
    store = open_store(tmp_path / "raw-reuse.db", actor="cursor", clock=FrozenClock(T0))
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
    assert result["launcher"] == "cursor"
    assert ledger_fingerprint(store) == before
    row = store.resolve_session(existing)
    assert row["ended_utc"] is None
    assert row["launcher"] is None
    store.conn.close()


def test_raw_admit_without_launcher_still_succeeds(tmp_path: Path) -> None:
    store = open_store(tmp_path / "raw.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    result = admit_agent(
        store,
        "oem-radar",
        mission["display_id"],
        actor="cursor",
        include_github=False,
        now=T0,
    )
    row = store.resolve_session(result["session_id"])
    assert result["launcher"] is None
    assert row["launcher"] is None
    assert row["context_fingerprint"]
    store.conn.close()


def test_cli_launch_requires_command_before_mutation(tmp_path: Path) -> None:
    db = str(tmp_path / "cli-req.db")
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    _paused(store)
    before = ledger_fingerprint(store)
    store.conn.close()
    with pytest.raises(SystemExit):
        main(["--db", db, "--actor", "cursor", "agent", "launch", "oem-radar", "--no-github"])
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_cli_launch_json_binds_session(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "cli-launch.db")
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    store.conn.close()
    assert (
        main(
            [
                "--db",
                db,
                "--json",
                "--actor",
                "cursor",
                "agent",
                "launch",
                "oem-radar",
                "--launcher",
                "cursor",
                "--no-github",
                "--command",
                sys.executable,
                "-c",
                "pass",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["launched"] is True
    assert payload["launcher"] == "cursor"
    assert payload["session_id"]
    assert payload["env"]["CLANKOPS_SESSION_ID"] == payload["session_id"]
    assert payload["env"]["CLANKOPS_MISSION"] == mission["display_id"]
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    row = store.resolve_session(payload["session_id"])
    assert row["launcher"] == "cursor"
    store.conn.close()


def test_wrappers_share_core_contract_and_contain_no_credentials() -> None:
    dev = DEV_WRAPPER.read_text(encoding="utf-8")
    assert '@("agent", "launch"' in dev.replace(" ", "") or '"agent", "launch"' in dev
    assert "@($AgentCommand)" in dev
    assert "Invoke-Expression" not in dev
    assert "AMBIGUOUS" not in dev
    assert "NO_UNFINISHED_MISSION" not in dev
    assert SECRET_RE.search(dev) is None
    for path in AGENT_WRAPPERS:
        text = path.read_text(encoding="utf-8")
        assert path.stem in ("cursor", "codex", "glm", "grok")
        assert "launch" in text
        assert "No credentials" in text or "No credentials." in text
        assert "AMBIGUOUS" not in text
        assert "NO_UNFINISHED_MISSION" not in text
        assert "Invoke-Expression" not in text
        assert SECRET_RE.search(text) is None
        assert "api_key" not in text.lower()


def test_standalone_prepare_status_values_unchanged(tmp_path: Path) -> None:
    store = open_store(tmp_path / "stand.db", actor="cursor", clock=FrozenClock(T0))
    first = _seed(store)
    assert _pkt(store)["admission"]["status"] == STATUS_RESUMABLE
    store.pause_mission(first["display_id"])
    store.start_mission("oem-radar", "second")
    assert prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)[
        "admission"
    ]["status"] == STATUS_AMBIGUOUS
    store.abandon_mission(first["display_id"])
    remaining = store.unfinished_missions("oem-radar")
    store.abandon_mission(remaining[0]["display_id"])
    assert prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)[
        "admission"
    ]["status"] == STATUS_NO_UNFINISHED_MISSION
    store.conn.close()


def test_unrelated_session_elsewhere_does_not_fail_managed_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "unrelated.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    radar = _paused(store)
    store.register_clank("watch-clank")
    watch = store.start_mission("watch-clank", "other track")
    store.conn.close()
    original = Store.start_fresh_launch_session

    def interfere(self, *args, **kwargs):
        other = open_store(db, actor="other", clock=FrozenClock(T0))
        other.start_session(watch["mission_id"], actor="other")
        other.conn.close()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "start_fresh_launch_session", interfere)
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    runner = FakeProc()
    result = launch_agent(
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
    assert result["launched"] is True
    assert len(runner.calls) == 1
    row = store.resolve_session(result["session_id"])
    assert row["launcher"] == "cursor"
    assert row["mission_id"] == radar["mission_id"]
    started = [event for event in _session_started(store) if event.session_id == result["session_id"]]
    assert len(started) == 1
    assert started[0].payload.get("launcher") == "cursor"
    store.conn.close()


def test_concurrent_same_actor_launches_create_one_session_and_one_spawn(tmp_path: Path) -> None:
    db = tmp_path / "conc.db"
    setup = open_store(db, actor="cursor", clock=FrozenClock(T0))
    mission = _paused(setup)
    setup.conn.close()
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, object]] = []
    lock = threading.Lock()

    def run() -> None:
        store = open_store(db, actor="cursor")
        runner = FakeProc()
        try:
            barrier.wait(timeout=10)
            result = launch_agent(
                store,
                "oem-radar",
                actor="cursor",
                launcher="cursor",
                command=["agent"],
                include_github=False,
                environ={},
                runner=runner,
            )
            with lock:
                outcomes.append(("ok", result, runner))
        except ValidationError as exc:
            with lock:
                outcomes.append(("err", exc, runner))
        finally:
            store.conn.close()

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    wins = [row for row in outcomes if row[0] == "ok"]
    losses = [row for row in outcomes if row[0] == "err"]
    assert len(wins) == 1
    assert len(losses) == 1
    assert len(wins[0][2].calls) == 1
    assert losses[0][2].calls == []
    assert "open Session already exists" in str(losses[0][1])
    store = open_store(db, actor="cursor")
    opened = _open_session_ids(store, mission["clank_id"])
    assert opened == [wins[0][1]["session_id"]]
    row = store.resolve_session(wins[0][1]["session_id"])
    assert row["launcher"] == "cursor"
    store.conn.close()


def test_concurrent_different_actors_both_launch(tmp_path: Path) -> None:
    db = tmp_path / "actors.db"
    setup = open_store(db, actor="cursor", clock=FrozenClock(T0))
    mission = _paused(setup)
    setup.conn.close()
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, dict, FakeProc]] = []
    lock = threading.Lock()

    def run(actor: str) -> None:
        store = open_store(db, actor=actor)
        runner = FakeProc()
        barrier.wait(timeout=10)
        result = launch_agent(
            store,
            "oem-radar",
            actor=actor,
            launcher=actor,
            command=["agent"],
            include_github=False,
            environ={},
            runner=runner,
        )
        with lock:
            outcomes.append((actor, result, runner))
        store.conn.close()

    threads = [
        threading.Thread(target=run, args=("cursor",)),
        threading.Thread(target=run, args=("glm",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert {row[0] for row in outcomes} == {"cursor", "glm"}
    assert all(row[2].calls for row in outcomes)
    ids = {row[1]["session_id"] for row in outcomes}
    assert len(ids) == 2
    store = open_store(db, actor="cursor")
    assert set(_open_session_ids(store, mission["clank_id"])) == ids
    store.conn.close()
