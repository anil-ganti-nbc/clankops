"""Foundation 8: derived resume packets and agent admission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clankops.agent import admit_agent, prepare_agent
from clankops.attention import attention_report
from clankops.cli import main
from clankops.clock import FrozenClock
from clankops.deployment import capture_deployment
from clankops.errors import InvalidTransitionError, NotFoundError, ValidationError
from clankops.projections import rebuild_projections
from clankops.readmodel import ledger_fingerprint
from clankops.resume import (
    STATUS_AMBIGUOUS,
    STATUS_NO_UNFINISHED_MISSION,
    STATUS_RESUMABLE,
    context_fingerprint,
    format_resume_text,
    resume_packet,
)
from clankops.store import open_readonly_store, open_store

from test_foundation2 import T0
from test_foundation3 import CANON, HEAD, OTHER, _github, _local, _seed
from test_foundation6 import HETZNER_SURFACE, NAS_SURFACE, _hetzner, _nas
from test_foundation7 import REASON_MISSION_NO_NEXT_ACTION, _attach_ci

FORBIDDEN_SUMMARY_WORDS = ("in summary", "the agent should", "health score")


def _pkt(store, clank="oem-radar", **kwargs):
    kwargs.setdefault("include_github", False)
    kwargs.setdefault("now", T0)
    return resume_packet(store, clank, **kwargs)


def _open_session_ids(store, clank_id: str) -> list[str]:
    rows = store.conn.execute(
        """
        SELECT session_id FROM sessions
        WHERE clank_id = ? AND ended_utc IS NULL
        ORDER BY started_utc, session_id
        """,
        (clank_id,),
    )
    return [row["session_id"] for row in rows]


def test_one_unfinished_mission_is_the_resumable_candidate(tmp_path: Path) -> None:
    store = open_store(tmp_path / "one.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(mission["display_id"], next_action="keep collecting")
    store.add_task(mission["display_id"], "write resume packet")
    store.add_blocker(mission["display_id"], "need operator review")
    store.add_decision(mission["display_id"], "packet is derived, not authoritative")
    packet = _pkt(store)
    assert packet["admission"]["status"] == STATUS_RESUMABLE
    assert packet["admission"]["resumable_mission"] == mission["display_id"]
    assert packet["admission"]["resumable_mission_id"] == mission["mission_id"]
    assert len(packet["unfinished_missions"]) == 1
    row = packet["unfinished_missions"][0]
    assert row["next_action"] == "keep collecting"
    assert row["tasks"][0]["title"] == "write resume packet"
    assert row["blockers"][0]["description"] == "need operator review"
    assert row["decisions"][0]["statement"] == "packet is derived, not authoritative"
    prepared = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    assert prepared["admission"]["status"] == STATUS_RESUMABLE
    assert prepared["wrote_events"] is False
    store.conn.close()


def test_two_unfinished_missions_are_ambiguous(tmp_path: Path) -> None:
    store = open_store(tmp_path / "two.db", actor="cursor", clock=FrozenClock(T0))
    first = _seed(store, remotes=[CANON])
    store.pause_mission(first["display_id"])
    second = store.start_mission("oem-radar", "second objective")
    packet = _pkt(store)
    assert packet["admission"]["status"] == STATUS_AMBIGUOUS
    assert packet["admission"]["resumable_mission"] is None
    ids = {row["mission"] for row in packet["unfinished_missions"]}
    assert ids == {first["display_id"], second["display_id"]}
    prepared = prepare_agent(store, "oem-radar", actor="codex", include_github=False, now=T0)
    assert prepared["admission"]["status"] == STATUS_AMBIGUOUS
    assert prepared["admission"]["resumable_mission"] is None
    store.conn.close()


def test_zero_unfinished_missions_is_explicit(tmp_path: Path) -> None:
    store = open_store(tmp_path / "zero.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    store.complete_mission(mission["display_id"])
    packet = _pkt(store)
    assert packet["admission"]["status"] == STATUS_NO_UNFINISHED_MISSION
    assert packet["unfinished_missions"] == []
    prepared = prepare_agent(store, "oem-radar", actor="glm", include_github=False, now=T0)
    assert prepared["admission"]["status"] == STATUS_NO_UNFINISHED_MISSION
    store.conn.close()


def test_packet_contains_foundation7_attention_unchanged(tmp_path: Path) -> None:
    store = open_store(tmp_path / "attn.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store)
    expected = attention_report(store, "oem-radar", now=T0, include_github=False)
    packet = _pkt(store)
    assert packet["attention"] == expected["items"]
    assert any(item["reason_code"] == REASON_MISSION_NO_NEXT_ACTION for item in packet["attention"])
    store.conn.close()


def test_packet_preserves_multiple_open_sessions(tmp_path: Path) -> None:
    store = open_store(tmp_path / "sess.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    grok = store.start_session(mission["mission_id"], actor="grok")
    packet = _pkt(store)
    actors = {row["actor"] for row in packet["open_sessions"]}
    ids = {row["session_id"] for row in packet["open_sessions"]}
    assert actors == {"cursor", "grok"}
    assert mission["session_id"] in ids
    assert grok["session_id"] in ids
    assert len(packet["open_sessions"]) == 2
    store.conn.close()


def test_packet_preserves_deployment_surfaces_and_mission_attribution(tmp_path: Path) -> None:
    store = open_store(tmp_path / "dep.db", actor="cursor", clock=FrozenClock(T0))
    first = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.pause_mission(first["display_id"])
    second = store.start_mission("oem-radar", "canary track")
    capture_deployment(store, "oem-radar", mission=first["display_id"], **_hetzner())
    capture_deployment(store, "oem-radar", mission=second["display_id"], **_nas())
    packet = _pkt(store)
    by_surface = {row["surface_id"]: row for row in packet["deployments"]}
    assert set(by_surface) == {HETZNER_SURFACE, NAS_SURFACE}
    assert by_surface[HETZNER_SURFACE]["mission"] == first["display_id"]
    assert by_surface[HETZNER_SURFACE]["mission_id"] == first["mission_id"]
    assert by_surface[NAS_SURFACE]["mission"] == second["display_id"]
    assert by_surface[NAS_SURFACE]["mission_id"] == second["mission_id"]
    store.conn.close()


def test_ci_is_per_mission_without_cross_attribution(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci.db", actor="cursor", clock=FrozenClock(T0))
    first = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.pause_mission(first["display_id"])
    second = store.start_mission("oem-radar", "other track")
    _attach_ci(store, first["display_id"], HEAD)
    packet = _pkt(store)
    by_mission = {row["mission"]: row for row in packet["unfinished_missions"]}
    assert by_mission[first["display_id"]]["ci"]["sha"] == HEAD
    assert by_mission[second["display_id"]]["ci"] is None
    store.conn.close()


def test_unobservable_git_stays_unknown(tmp_path: Path) -> None:
    store = open_store(tmp_path / "unk.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    packet = _pkt(store)
    assert packet["observation"]["ok"] is False
    assert packet["observation"]["branch"] is None
    assert packet["observation"]["head"] is None
    assert packet["observation"]["working_tree"] is None
    assert packet["reconcile"]["status"] in {"unknown", "no-record"}
    text = format_resume_text(packet)
    assert "Observed: unknown" in text
    assert "failed" not in text.lower()
    store.conn.close()


def test_packet_generation_does_not_change_ledger_fingerprint(tmp_path: Path) -> None:
    db = tmp_path / "fp.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    _seed(store)
    before = ledger_fingerprint(store)
    store.conn.close()
    readonly = open_readonly_store(db, clock=FrozenClock(T0))
    _pkt(readonly)
    prepare_agent(readonly, "oem-radar", actor="cursor", include_github=False, now=T0)
    readonly.conn.close()
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_identical_projection_state_same_context_fingerprint(tmp_path: Path) -> None:
    store = open_store(tmp_path / "same.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    first = _pkt(store)
    second = _pkt(store)
    assert first["context_fingerprint"] == second["context_fingerprint"]
    assert first["context_fingerprint"] == context_fingerprint(first)
    cursor = prepare_agent(store, "oem-radar", actor="cursor", include_github=False, now=T0)
    grok = prepare_agent(store, "oem-radar", actor="grok", include_github=False, now=T0)
    assert cursor["context_fingerprint"] == grok["context_fingerprint"]
    assert cursor["requested_actor"] == "cursor"
    assert grok["requested_actor"] == "grok"
    store.conn.close()


def test_relevant_state_change_changes_context_fingerprint(tmp_path: Path) -> None:
    store = open_store(tmp_path / "change.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    before = _pkt(store)["context_fingerprint"]
    store.record_checkpoint(mission["display_id"], next_action="write the packet")
    after = _pkt(store)["context_fingerprint"]
    assert after != before
    store.conn.close()


def test_stale_expect_context_fails_before_session_creation(tmp_path: Path) -> None:
    store = open_store(tmp_path / "stale.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    store.pause_mission(mission["display_id"])
    stale = _pkt(store)["context_fingerprint"]
    store.record_checkpoint(mission["display_id"], next_action="changed")
    before = ledger_fingerprint(store)
    open_before = _open_session_ids(store, mission["clank_id"])
    with pytest.raises(ValidationError, match="stale context"):
        admit_agent(
            store,
            "oem-radar",
            mission["display_id"],
            actor="cursor",
            expect_context=stale,
            include_github=False,
            now=T0,
        )
    assert ledger_fingerprint(store) == before
    assert _open_session_ids(store, mission["clank_id"]) == open_before
    store.conn.close()


def test_valid_admission_opens_exactly_one_session(tmp_path: Path) -> None:
    store = open_store(tmp_path / "admit.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store)
    store.pause_mission(mission["display_id"])
    assert _open_session_ids(store, mission["clank_id"]) == []
    packet = _pkt(store)
    result = admit_agent(
        store,
        "oem-radar",
        mission["display_id"],
        actor="cursor",
        expect_context=packet["context_fingerprint"],
        include_github=False,
        now=T0,
    )
    opened = _open_session_ids(store, mission["clank_id"])
    assert opened == [result["session_id"]]
    assert result["mission"] == mission["display_id"]
    assert result["mission_state"] == "ACTIVE"
    assert result["session"]["actor"] == "cursor"
    store.conn.close()


def test_invalid_mission_clank_actor_attribution_fails(tmp_path: Path) -> None:
    store = open_store(tmp_path / "bad.db", actor="cursor", clock=FrozenClock(T0))
    radar = _seed(store)
    store.register_clank("watch-clank")
    watch = store.start_mission("watch-clank", "keep collecting")
    store.complete_mission(radar["display_id"])
    before = ledger_fingerprint(store)
    with pytest.raises(NotFoundError):
        admit_agent(store, "missing-clank", radar["display_id"], actor="cursor", include_github=False)
    with pytest.raises(NotFoundError):
        admit_agent(store, "oem-radar", "COPS-999999", actor="cursor", include_github=False)
    with pytest.raises(ValidationError, match="belongs to"):
        admit_agent(
            store,
            "oem-radar",
            watch["display_id"],
            actor="cursor",
            include_github=False,
        )
    with pytest.raises(ValidationError, match="actor is required"):
        admit_agent(store, "watch-clank", watch["display_id"], actor="  ", include_github=False)
    with pytest.raises(InvalidTransitionError):
        admit_agent(
            store,
            "oem-radar",
            radar["display_id"],
            actor="cursor",
            include_github=False,
        )
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_projection_rebuild_gives_equivalent_packet_fingerprint(tmp_path: Path) -> None:
    store = open_store(tmp_path / "rebuild.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="main",
        head=HEAD,
    )
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    _attach_ci(store, mission["display_id"], HEAD)
    capture_deployment(store, "oem-radar", **_hetzner())
    inspect = lambda _path: _local("main", HEAD, dirty=False)
    before = resume_packet(
        store, "oem-radar", include_github=False, now=T0, inspect_local=inspect
    )
    rebuild_projections(store.conn)
    after = resume_packet(
        store, "oem-radar", include_github=False, now=T0, inspect_local=inspect
    )
    assert after["context_fingerprint"] == before["context_fingerprint"]
    assert after["unfinished_missions"][0]["mission"] == before["unfinished_missions"][0]["mission"]
    store.conn.close()


def test_cli_text_and_json_agree_on_identities(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "cli.db")
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    mission = store.register_clank("oem-radar", display_name="OEM Radar")
    started = store.start_mission("oem-radar", "handheld")
    store.start_session(started["mission_id"], actor="grok")
    store.record_checkpoint(started["display_id"], next_action="keep collecting")
    store.conn.close()

    assert main(["--db", db, "--json", "resume-packet", "oem-radar", "--no-github"]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert main(["--db", db, "resume-packet", "oem-radar", "--no-github"]) == 0
    text = capsys.readouterr().out
    assert packet["identity"]["clank_id"] == mission["clank_id"]
    assert started["display_id"] in text
    assert started["display_id"] == packet["unfinished_missions"][0]["mission"]
    assert started["mission_id"] == packet["unfinished_missions"][0]["mission_id"]
    for row in packet["open_sessions"]:
        assert row["session_id"] in text
    assert packet["context_fingerprint"] in text
    assert text.startswith("OEM Radar")
    assert "Admission: RESUMABLE" in text
    for word in FORBIDDEN_SUMMARY_WORDS:
        assert word not in text.lower()

    assert main(
        ["--db", db, "--json", "agent", "prepare", "oem-radar", "--actor", "glm", "--no-github"]
    ) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["requested_actor"] == "glm"
    assert prepared["context_fingerprint"] == packet["context_fingerprint"]
    assert prepared["admission"]["status"] == STATUS_RESUMABLE
    store.conn.close()


def test_cli_admit_binds_session_and_rejects_stale(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "admit-cli.db")
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", display_name="OEM Radar")
    started = store.start_mission("oem-radar", "handheld")
    store.pause_mission(started["display_id"])
    store.conn.close()

    assert main(
        ["--db", db, "--json", "agent", "prepare", "oem-radar", "--actor", "cursor", "--no-github"]
    ) == 0
    prepared = json.loads(capsys.readouterr().out)
    stale = prepared["context_fingerprint"]
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.record_checkpoint(started["display_id"], next_action="changed after prepare")
    before = ledger_fingerprint(store)
    store.conn.close()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "agent",
                "admit",
                "oem-radar",
                "--mission",
                started["display_id"],
                "--expect-context",
                stale,
                "--no-github",
            ]
        )
        == 2
    )
    err = capsys.readouterr().err
    assert "stale context" in err
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    assert ledger_fingerprint(store) == before
    assert _open_session_ids(store, started["clank_id"]) == []
    fresh = _pkt(store)["context_fingerprint"]
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
                "admit",
                "oem-radar",
                "--mission",
                started["display_id"],
                "--expect-context",
                fresh,
                "--no-github",
            ]
        )
        == 0
    )
    admitted = json.loads(capsys.readouterr().out)
    assert admitted["admitted"] is True
    assert admitted["mission_display"] == started["display_id"]
    assert admitted["session_id"]
    assert admitted["context_fingerprint"] == fresh
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    assert _open_session_ids(store, started["clank_id"]) == [admitted["session_id"]]
    store.conn.close()


def test_observed_git_is_included_when_observable(tmp_path: Path) -> None:
    store = open_store(tmp_path / "obs.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="main",
        head=HEAD,
        working_tree="clean",
    )
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
        working_tree="clean",
    )
    packet = resume_packet(
        store,
        "oem-radar",
        include_github=False,
        now=T0,
        inspect_local=lambda _path: _local("main", HEAD, dirty=False),
    )
    assert packet["observation"]["ok"] is True
    assert packet["observation"]["branch"] == "main"
    assert packet["observation"]["head"] == HEAD
    assert packet["reconcile"]["status"] == "aligned"
    assert "identity" in packet
    assert packet["identity"]["github_repo"] is not None
    store.conn.close()


def test_github_inspect_is_optional_and_unused_when_disabled(tmp_path: Path) -> None:
    store = open_store(tmp_path / "gh.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    called = []

    def remote(repo):
        called.append(repo)
        return _github()(repo)

    packet = resume_packet(
        store,
        "oem-radar",
        include_github=False,
        now=T0,
        inspect_remote=remote,
    )
    assert called == []
    assert packet["wrote_events"] is False
    store.conn.close()


def test_example_adapters_are_thin_and_contain_no_credentials() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "agents"
    forbidden = ("api_key", "apikey", "secret", "password", "ghp_", "sk-")
    for name in ("cursor.ps1", "codex.ps1", "glm.ps1", "grok.ps1"):
        text = (root / name).read_text(encoding="utf-8")
        lower = text.lower()
        assert "clankops-dev.ps1" in lower
        assert "json" in lower
        for word in forbidden:
            assert word not in lower, f"{name} contains {word}"
