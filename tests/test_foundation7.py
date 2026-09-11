"""Foundation 7: derived attention queue and evidence freshness."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from clankops.attention import (
    REASON_CI_EVIDENCE_BEHIND_MISSION,
    REASON_DEPLOYMENT_DIFFERS_FROM_MISSION,
    REASON_DIRTY_WITHOUT_OPEN_SESSION,
    REASON_GIT_DRIFT,
    REASON_MISSION_NO_NEXT_ACTION,
    REASON_STALE_OPEN_SESSION,
    attention_report,
    format_attention_text,
)
from clankops.ci import CI_ARTIFACT_KIND
from clankops.cli import main
from clankops.clock import FrozenClock
from clankops.deployment import capture_deployment
from clankops.enums import EventSource
from clankops.projections import rebuild_projections
from clankops.readmodel import DEFAULT_STALE, fleet_home, ledger_fingerprint
from clankops.store import open_readonly_store, open_store
from clankops.terminal import _fleet_html, dispatch

from test_foundation2 import T0
from test_foundation3 import CANON, HEAD, OTHER, _local, _seed
from test_foundation6 import HETZNER_SHA, HETZNER_SURFACE, NAS_SURFACE, _hetzner, _nas

FORBIDDEN_DEPLOY_WORDS = ("deployment is stale", "deployment failed", "outdated deployment")


def _attn(store, clank=None, **kwargs):
    kwargs.setdefault("include_github", False)
    kwargs.setdefault("now", T0)
    return attention_report(store, clank, **kwargs)


def _codes(report) -> list[str]:
    return [item["reason_code"] for item in report["items"]]


def _of(report, code: str) -> list[dict]:
    return [item for item in report["items"] if item["reason_code"] == code]


def _attach_ci(store, mission: str, sha: str):
    return store.attach_artifact(
        mission=mission,
        kind=CI_ARTIFACT_KIND,
        ref=sha,
        title=f"CI artefact {sha[:7]}",
        source=EventSource.CI,
        artifact_source=EventSource.CI,
        metadata={"sha": sha, "recorded_sha": HEAD, "sha_attribution": "checkpoint_head"},
    )


def test_unfinished_mission_without_next_action_appears(tmp_path: Path) -> None:
    store = open_store(tmp_path / "none.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("watch-clank", display_name="Watch Clank")
    mission = store.start_mission("watch-clank", "keep collecting")
    report = _attn(store, "watch-clank")
    items = _of(report, REASON_MISSION_NO_NEXT_ACTION)
    assert len(items) == 1
    assert items[0]["clank"] == "watch-clank"
    assert items[0]["clank_name"] == "Watch Clank"
    assert items[0]["mission"] == mission["display_id"]
    assert f"{mission['display_id']} has no recorded next action" in items[0]["reason"]
    assert items[0]["suggested_action"]
    assert items[0]["source"]
    assert items[0]["provenance"]
    store.conn.close()


def test_stale_open_session_uses_existing_semantics(tmp_path: Path) -> None:
    store = open_store(tmp_path / "stale.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("watch-clank", display_name="Watch Clank")
    store.start_mission("watch-clank", "keep collecting")
    fresh = _attn(
        store,
        "watch-clank",
        now=T0 + timedelta(hours=23),
        stale_after=DEFAULT_STALE,
    )
    assert _of(fresh, REASON_STALE_OPEN_SESSION) == []
    aged = _attn(
        store,
        "watch-clank",
        now=T0 + timedelta(hours=24),
        stale_after=DEFAULT_STALE,
        threshold_label="24h",
        threshold_source="session-staleness default",
    )
    items = _of(aged, REASON_STALE_OPEN_SESSION)
    assert len(items) == 1
    assert "session-staleness default" in items[0]["reason"]
    assert items[0]["threshold_source"] == "session-staleness default"
    operator = _attn(
        store,
        "watch-clank",
        now=T0 + timedelta(hours=24),
        stale_after=timedelta(hours=72),
        threshold_label="72h",
        threshold_source="operator-supplied",
    )
    assert _of(operator, REASON_STALE_OPEN_SESSION) == []
    assert operator["threshold_source"] == "operator-supplied"
    store.conn.close()


def test_aligned_git_is_not_drift(tmp_path: Path) -> None:
    store = open_store(tmp_path / "aligned.db", actor="cursor", clock=FrozenClock(T0))
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
    report = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD, dirty=False),
    )
    assert REASON_GIT_DRIFT not in _codes(report)
    store.conn.close()


def test_git_drift_appears(tmp_path: Path) -> None:
    store = open_store(tmp_path / "drift.db", actor="cursor", clock=FrozenClock(T0))
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
    report = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", OTHER, dirty=False),
    )
    items = _of(report, REASON_GIT_DRIFT)
    assert len(items) == 1
    assert "recorded" in items[0]["reason"]
    assert "!=" in items[0]["reason"]
    assert items[0]["evidence"]["field"] == "head"
    assert items[0]["evidence"]["recorded"] == HEAD
    assert items[0]["evidence"]["observed"] == OTHER
    assert "head" in items[0]["reason"]
    store.conn.close()


def test_dirty_with_open_session_does_not_emit_dirty_without_session(tmp_path: Path) -> None:
    store = open_store(tmp_path / "dirty-open.db", actor="cursor", clock=FrozenClock(T0))
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
    report = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD, dirty=True),
    )
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION not in _codes(report)
    store.conn.close()


def test_dirty_without_open_session_does_emit(tmp_path: Path) -> None:
    store = open_store(tmp_path / "dirty-closed.db", actor="cursor", clock=FrozenClock(T0))
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
    store.pause_mission(mission["display_id"])
    report = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD, dirty=True),
    )
    items = _of(report, REASON_DIRTY_WITHOUT_OPEN_SESSION)
    assert len(items) == 1
    assert "no open Session" in items[0]["reason"]
    assert REASON_GIT_DRIFT not in _codes(report)
    store.conn.close()


def test_unobservable_git_is_not_dirty_or_drift(tmp_path: Path) -> None:
    store = open_store(tmp_path / "unknown.db", actor="cursor", clock=FrozenClock(T0))
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
    missing = _attn(store, "oem-radar")  # path exists on disk but is not a git checkout
    assert REASON_GIT_DRIFT not in _codes(missing)
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION not in _codes(missing)
    dead = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: {
            "is_git": False,
            "git_error": "missing",
            "current_branch": None,
            "head": None,
            "dirty": None,
        },
    )
    assert REASON_GIT_DRIFT not in _codes(dead)
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION not in _codes(dead)
    store.conn.close()


def test_matching_ci_sha_is_not_behind(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-ok.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    _attach_ci(store, mission["display_id"], HEAD)
    report = _attn(store, "oem-radar")
    assert REASON_CI_EVIDENCE_BEHIND_MISSION not in _codes(report)
    store.conn.close()


def test_missing_ci_artefact_is_not_behind(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-none.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    report = _attn(store, "oem-radar")
    assert REASON_CI_EVIDENCE_BEHIND_MISSION not in _codes(report)
    store.conn.close()


def test_different_ci_sha_is_behind_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-behind.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    artefact = _attach_ci(store, mission["display_id"], OTHER)
    report = _attn(store, "oem-radar")
    items = _of(report, REASON_CI_EVIDENCE_BEHIND_MISSION)
    assert len(items) == 1
    assert items[0]["evidence"]["artifact_id"] == artefact["artifact_id"]
    assert items[0]["evidence"]["artefact_sha"] == OTHER
    assert items[0]["evidence"]["recorded_head"] == HEAD
    store.conn.close()


def test_two_deployment_surfaces_are_separately_attributable(tmp_path: Path) -> None:
    store = open_store(tmp_path / "deploy.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    capture_deployment(store, "oem-radar", **_hetzner())
    capture_deployment(store, "oem-radar", **_nas())
    report = _attn(store, "oem-radar")
    items = _of(report, REASON_DEPLOYMENT_DIFFERS_FROM_MISSION)
    surfaces = {item["evidence"]["surface_id"] for item in items}
    assert surfaces == {HETZNER_SURFACE, NAS_SURFACE}
    for item in items:
        reason = item["reason"].lower()
        assert "deployment differs from recorded mission head" in reason
        for phrase in FORBIDDEN_DEPLOY_WORDS:
            assert phrase not in reason
        assert item["class"] == "informational"
    text = format_attention_text(report)
    assert HETZNER_SURFACE in text
    assert "24d61dd" in text
    store.conn.close()


def test_matching_deployed_sha_does_not_appear(tmp_path: Path) -> None:
    store = open_store(tmp_path / "deploy-ok.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    capture_deployment(store, "oem-radar", **_hetzner(deployed_sha=HEAD))
    report = _attn(store, "oem-radar")
    assert _of(report, REASON_DEPLOYMENT_DIFFERS_FROM_MISSION) == []
    store.conn.close()


def test_attention_writes_zero_ledger_events(tmp_path: Path) -> None:
    db = tmp_path / "fp.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    mission = _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="main",
        head=HEAD,
    )
    store.pause_mission(mission["display_id"])
    before = ledger_fingerprint(store)
    report = _attn(
        store,
        inspect_local=lambda _path: _local("main", OTHER, dirty=True),
    )
    assert report["items"]
    assert ledger_fingerprint(store) == before
    readonly = open_readonly_store(db, clock=FrozenClock(T0))
    again = _attn(
        readonly,
        inspect_local=lambda _path: _local("main", OTHER, dirty=True),
    )
    assert ledger_fingerprint(readonly) == before
    assert _codes(again) == _codes(report)
    readonly.conn.close()
    store.conn.close()


def test_projection_rebuild_yields_the_same_derived_attention(tmp_path: Path) -> None:
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
    _attach_ci(store, mission["display_id"], OTHER)
    capture_deployment(store, "oem-radar", **_hetzner())
    capture_deployment(store, "oem-radar", **_nas())
    def inspect(_path):
        return _local("main", OTHER, dirty=False)

    before = _attn(store, "oem-radar", inspect_local=inspect)
    rebuild_projections(store.conn)
    after = _attn(store, "oem-radar", inspect_local=inspect)
    assert json.dumps(before["items"], sort_keys=True, default=str) == json.dumps(
        after["items"], sort_keys=True, default=str
    )
    assert json.dumps(before["freshness"], sort_keys=True, default=str) == json.dumps(
        after["freshness"], sort_keys=True, default=str
    )
    store.conn.close()


def test_ordering_integrity_then_informational_then_age(tmp_path: Path) -> None:
    store = open_store(tmp_path / "order.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank(
        "oem-radar",
        display_name="OEM Radar",
        local_path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
    )
    mission = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(mission["display_id"], branch="main", head=HEAD)
    capture_deployment(store, "oem-radar", **_hetzner())
    report = _attn(
        store,
        now=T0 + timedelta(hours=24),
        inspect_local=lambda _path: _local("main", OTHER, dirty=False),
    )
    classes = [item["class"] for item in report["items"]]
    integrity = [i for i, klass in enumerate(classes) if klass == "integrity"]
    informational = [i for i, klass in enumerate(classes) if klass == "informational"]
    age = [i for i, klass in enumerate(classes) if klass == "age"]
    assert integrity
    assert informational
    assert age
    assert max(integrity) < min(informational) < min(age)
    store.conn.close()


def test_freshness_exposes_ages_without_turning_them_into_verdicts(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ages.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
    )
    _attach_ci(store, mission["display_id"], HEAD)
    capture_deployment(store, "oem-radar", **_hetzner(deployed_sha=HEAD))
    report = _attn(store, "oem-radar", now=T0 + timedelta(hours=3))
    assert len(report["freshness"]) == 1
    row = report["freshness"][0]
    assert len(row["missions"]) == 1
    assert row["missions"][0]["checkpoint_utc"]
    assert row["missions"][0]["checkpoint_age"]
    assert row["open_session_age"]
    assert row["missions"][0]["ci_capture_age"]
    assert row["deployments"][0]["age"]
    assert REASON_CI_EVIDENCE_BEHIND_MISSION not in _codes(report)
    assert REASON_DEPLOYMENT_DIFFERS_FROM_MISSION not in _codes(report)
    store.conn.close()


def test_terminal_renders_reason_codes_without_relying_on_colour(tmp_path: Path) -> None:
    store = open_store(tmp_path / "term.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank(
        "oem-radar",
        display_name="OEM Radar",
        local_path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
    )
    oem = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(oem["display_id"], branch="main", head=HEAD)
    capture_deployment(store, "oem-radar", **_hetzner())
    store.register_clank("watch-clank", display_name="Watch Clank")
    watch = store.start_mission("watch-clank", "keep collecting")
    home = fleet_home(
        store,
        now=T0,
        include_github=False,
        inspect_local=lambda _path: _local("main", OTHER, dirty=False),
    )
    html = _fleet_html(home)
    assert "ATTENTION" in html
    assert "[GIT_DRIFT]" in html
    assert "[MISSION_NO_NEXT_ACTION]" in html
    assert "[DEPLOYMENT_DIFFERS_FROM_MISSION]" in html
    assert "recorded" in html and "!=" in html
    assert f"{watch['display_id']} has no recorded next action" in html
    assert "deployment differs from recorded Mission HEAD" in html
    assert HETZNER_SURFACE in html
    assert "24d61dd" in html
    assert "■" in html
    assert "◇" in html
    assert "attention-integrity" in html
    assert "attention-informational" in html
    status, ctype, body = dispatch(
        store,
        "GET",
        "/",
        now=T0,
    )
    assert status == 200
    page = body.decode("utf-8")
    assert "ATTENTION" in page
    assert "[GIT_DRIFT]" in page or "MISSION_NO_NEXT_ACTION" in page
    store.conn.close()


def test_cli_attention_json_and_operator_threshold(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "cli.db")
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("watch-clank", display_name="Watch Clank")
    mission = store.start_mission("watch-clank", "keep collecting")
    store.conn.close()
    assert main(["--db", db, "--json", "attention", "watch-clank", "--no-github"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["derived"] is True
    assert payload["authoritative"] is False
    assert payload["threshold_source"] == "session-staleness default"
    assert any(item["reason_code"] == REASON_MISSION_NO_NEXT_ACTION for item in payload["items"])
    assert any(mission["display_id"] in item["reason"] for item in payload["items"])
    assert main(
        [
            "--db",
            db,
            "attention",
            "watch-clank",
            "--no-github",
            "--older-than",
            "72h",
        ]
    ) == 0
    text = capsys.readouterr().out
    assert "operator-supplied" in text
    assert "72h" in text
    assert "MISSION_NO_NEXT_ACTION" in text
    assert mission["display_id"] in text
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    before = ledger_fingerprint(store)
    store.conn.close()
    assert main(["--db", db, "attention", "--no-github"]) == 0
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_git_drift_attributes_working_tree_not_head(tmp_path: Path) -> None:
    store = open_store(tmp_path / "tree-drift.db", actor="cursor", clock=FrozenClock(T0))
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
    report = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD, dirty=True),
    )
    items = _of(report, REASON_GIT_DRIFT)
    assert len(items) == 1
    assert items[0]["evidence"]["field"] == "working_tree"
    assert items[0]["source"] == EventSource.LOCAL_GIT
    assert "working_tree" in items[0]["reason"]
    assert "head" not in items[0]["reason"]
    assert HEAD not in items[0]["reason"]
    store.conn.close()


def test_git_drift_attributes_branch_not_head(tmp_path: Path) -> None:
    store = open_store(tmp_path / "branch-drift.db", actor="cursor", clock=FrozenClock(T0))
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
    report = _attn(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("feature/x", HEAD, dirty=False),
    )
    items = _of(report, REASON_GIT_DRIFT)
    assert len(items) == 1
    assert items[0]["evidence"]["field"] == "branch"
    assert items[0]["evidence"]["recorded"] == "main"
    assert items[0]["evidence"]["observed"] == "feature/x"
    assert "branch" in items[0]["reason"]
    assert "head" not in items[0]["reason"]
    store.conn.close()


def test_each_unfinished_mission_is_evaluated_for_ci(tmp_path: Path) -> None:
    store = open_store(tmp_path / "multi-ci.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", remotes=[CANON])
    older = store.start_mission("oem-radar", "first")
    store.record_checkpoint(
        older["display_id"],
        next_action="first next",
        branch="main",
        head=HEAD,
    )
    _attach_ci(store, older["display_id"], OTHER)
    store.pause_mission(older["display_id"])
    newer = store.start_mission("oem-radar", "second")
    store.record_checkpoint(
        newer["display_id"],
        next_action="second next",
        branch="main",
        head=HEAD,
    )
    _attach_ci(store, newer["display_id"], HEAD)
    unfinished = store.unfinished_missions("oem-radar")
    assert unfinished[0]["display_id"] == newer["display_id"]
    report = _attn(store, "oem-radar")
    items = _of(report, REASON_CI_EVIDENCE_BEHIND_MISSION)
    assert [item["mission"] for item in items] == [older["display_id"]]
    store.conn.close()


def test_each_unfinished_mission_is_evaluated_for_deployment(tmp_path: Path) -> None:
    store = open_store(tmp_path / "multi-dep.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", remotes=[CANON])
    older = store.start_mission("oem-radar", "first")
    store.record_checkpoint(
        older["display_id"],
        next_action="first next",
        branch="main",
        head=HEAD,
    )
    store.pause_mission(older["display_id"])
    newer = store.start_mission("oem-radar", "second")
    store.record_checkpoint(
        newer["display_id"],
        next_action="second next",
        branch="main",
        head=HETZNER_SHA,
    )
    capture_deployment(store, "oem-radar", mission=newer["display_id"], **_hetzner())
    unfinished = store.unfinished_missions("oem-radar")
    assert unfinished[0]["display_id"] == newer["display_id"]
    report = _attn(store, "oem-radar")
    items = _of(report, REASON_DEPLOYMENT_DIFFERS_FROM_MISSION)
    assert [item["mission"] for item in items] == [older["display_id"]]
    assert items[0]["evidence"]["surface_id"] == HETZNER_SURFACE
    store.conn.close()
