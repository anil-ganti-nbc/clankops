"""Foundation 5: record observed GitHub CI as Mission artefacts."""

from __future__ import annotations

import json
from pathlib import Path

from clankops.ci import CI_ARTIFACT_KIND, capture_ci
from clankops.enums import EventSource
from clankops.errors import ValidationError
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.reconcile import reconcile_clank
from clankops.store import open_store
from clankops.terminal import _dossier_html

from test_foundation3 import CANON, HEAD, OTHER, _github, _local, _seed

import pytest


def _remote_with_checks(*, state="success", runs=None, contexts=None, extras=None):
    def _inspect(repo):
        payload = _github()(repo)
        run_list = (
            runs
            if runs is not None
            else [
                {
                    "name": "test",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://example.test/ci",
                }
            ]
        )
        ctx_list = contexts if contexts is not None else []
        payload["checks"] = {
            "source": EventSource.GITHUB,
            "ok": True,
            "error": None,
            "repo": repo,
            "sha": HEAD,
            "state": state,
            "runs": run_list,
            "contexts": ctx_list,
            "checks_observed": True,
            "statuses_observed": True,
            "check_run_total": len(run_list),
            "checks_complete": True,
            "status_total": len(ctx_list),
            "statuses_complete": True,
            "combined_state": None,
            **(extras or {}),
        }
        return payload

    return _inspect


def _meta(artifact: dict) -> dict:
    raw = artifact.get("metadata_json") or artifact.get("metadata") or "{}"
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _artifact_event_count(store) -> int:
    row = store.conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = 'ARTIFACT_ATTACHED'"
    ).fetchone()
    return int(row["n"])


def test_ci_capture_attaches_artefact_without_rewriting_git_claims(tmp_path: Path) -> None:
    db = tmp_path / "cap.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    rec = reconcile_clank(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert rec["status"] == "aligned"
    assert ledger_fingerprint(store) == before

    result = capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    after = ledger_fingerprint(store)
    assert after["event_count"] == before["event_count"] + 1
    assert _artifact_event_count(store) == 1
    assert result["rewrote_history"] is False
    assert result["rewrote_git_claims"] is False
    assert result["ci_state"] == "success"
    assert result["sha"] == HEAD
    art = result["artifact"]
    assert art["kind"] == CI_ARTIFACT_KIND
    assert art["source"] == EventSource.CI
    assert art["ref"] == HEAD
    meta = _meta(art)
    assert meta["repo"] == "anil-ganti-nbc/oem-radar"
    assert meta["sha"] == HEAD
    assert meta["state"] == "success"
    assert meta["error"] is None
    assert meta["checks_observed"] is True
    assert meta["statuses_observed"] is True
    assert meta["check_run_total"] == 1
    assert meta["checks_complete"] is True
    assert meta["status_total"] == 0
    assert meta["statuses_complete"] is True
    assert "combined_state" in meta
    assert meta["runs"][0]["name"] == "test"
    assert meta["contexts"] == []
    assert meta["git_status"] == "aligned"
    assert meta["mission_display"] == result["mission_display"]
    assert meta["recorded_sha"] == HEAD
    assert meta["sha"] == HEAD
    assert meta["sha_attribution"] == "mission_checkpoint"
    rec_after = reconcile_clank(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert rec_after["status"] == rec["status"]
    assert rec_after["comparisons"] == rec["comparisons"]
    store.conn.close()


def test_ci_capture_records_failing_check_names(tmp_path: Path) -> None:
    db = tmp_path / "fail.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    result = capture_ci(
        store,
        "oem-radar",
        inspect_remote=_remote_with_checks(
            state="failure",
            runs=[{"name": "test", "status": "completed", "conclusion": "failure"}],
        ),
    )
    assert result["ci_state"] == "failure"
    assert "failing: test" in result["artifact"]["title"]
    rec = reconcile_clank(
        store,
        "oem-radar",
        inspect_remote=_remote_with_checks(state="failure"),
    )
    assert rec["status"] == "aligned"
    store.conn.close()


def test_ci_capture_records_status_context_failure_in_metadata(tmp_path: Path) -> None:
    db = tmp_path / "ctx-fail.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    remote = _remote_with_checks(
        state="failure",
        runs=[],
        contexts=[
            {
                "context": "legacy-jenkins",
                "state": "failure",
                "target_url": "https://example.test/jenkins/1",
                "description": "Build #1 failed",
            }
        ],
        extras={"combined_state": "failure", "check_run_total": 0, "status_total": 1},
    )
    before = ledger_fingerprint(store)
    rec = reconcile_clank(store, "oem-radar", inspect_remote=remote)
    git_status = rec["status"]
    result = capture_ci(store, "oem-radar", inspect_remote=remote)
    assert ledger_fingerprint(store)["event_count"] == before["event_count"] + 1
    art = result["artifact"]
    meta = _meta(art)
    assert result["ci_state"] == "failure"
    assert meta["state"] == "failure"
    assert meta["combined_state"] == "failure"
    assert meta["failing_contexts"] == ["legacy-jenkins"]
    assert meta["contexts"][0]["context"] == "legacy-jenkins"
    assert meta["contexts"][0]["state"] == "failure"
    assert meta["contexts"][0]["target_url"] == "https://example.test/jenkins/1"
    assert "legacy-jenkins" in art["title"]
    rec_after = reconcile_clank(store, "oem-radar", inspect_remote=remote)
    assert rec_after["status"] == git_status
    store.conn.close()


def test_ci_capture_records_observed_unknown_state(tmp_path: Path) -> None:
    db = tmp_path / "unknown.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    remote = _remote_with_checks(
        state="unknown",
        extras={
            "error": "check-run page truncated",
            "checks_complete": False,
            "checks_observed": True,
            "statuses_observed": True,
        },
    )
    result = capture_ci(store, "oem-radar", inspect_remote=remote)
    meta = _meta(result["artifact"])
    assert result["ci_state"] == "unknown"
    assert meta["state"] == "unknown"
    assert meta["error"] == "check-run page truncated"
    assert meta["checks_complete"] is False
    store.conn.close()


def test_ci_capture_refuses_when_nothing_was_observed(tmp_path: Path) -> None:
    db = tmp_path / "none-obs.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)

    def remote_without_checks(repo):
        return _github()(repo)

    with pytest.raises(ValidationError, match="no GitHub CI observation"):
        capture_ci(store, "oem-radar", inspect_remote=remote_without_checks)
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_refuses_local_sha_without_github_repo(tmp_path: Path) -> None:
    db = tmp_path / "local-only.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no GitHub repo"):
        capture_ci(store, "oem-radar")
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_requires_unfinished_mission(tmp_path: Path) -> None:
    db = tmp_path / "none.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar", remotes=[CANON])
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_refuses_completed_mission_without_explicit(tmp_path: Path) -> None:
    db = tmp_path / "completed.db"
    store = open_store(db, actor="cursor")
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.complete_mission(mission["display_id"])
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_refuses_abandoned_mission_without_explicit(tmp_path: Path) -> None:
    db = tmp_path / "abandoned.db"
    store = open_store(db, actor="cursor")
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.abandon_mission(mission["display_id"])
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_attaches_to_single_paused_mission(tmp_path: Path) -> None:
    db = tmp_path / "paused.db"
    store = open_store(db, actor="cursor")
    mission = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.pause_mission(mission["display_id"])
    result = capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert result["mission_display"] == mission["display_id"]
    store.conn.close()


def test_ci_capture_two_unfinished_missions_without_explicit_is_ambiguous(
    tmp_path: Path,
) -> None:
    db = tmp_path / "ambig.db"
    store = open_store(db, actor="cursor")
    first = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    second = store.start_mission("oem-radar", "second objective")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="multiple unfinished Missions"):
        capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert ledger_fingerprint(store) == before
    assert first["display_id"] != second["display_id"]
    store.conn.close()


def test_ci_capture_explicit_unfinished_mission_succeeds(tmp_path: Path) -> None:
    db = tmp_path / "explicit.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    second = store.start_mission("oem-radar", "second objective")
    store.record_checkpoint(
        second["display_id"],
        branch="main",
        head=HEAD,
        source=EventSource.AGENT_REPORT,
    )
    before = ledger_fingerprint(store)
    result = capture_ci(
        store,
        "oem-radar",
        mission=second["display_id"],
        inspect_remote=_remote_with_checks(),
    )
    assert result["mission_display"] == second["display_id"]
    assert ledger_fingerprint(store)["event_count"] == before["event_count"] + 1
    store.conn.close()


def test_ci_capture_explicit_mission_for_other_clank_fails(tmp_path: Path) -> None:
    db = tmp_path / "other.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.register_clank("watch-clank")
    other = store.start_mission("watch-clank", "health")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="belongs to watch-clank"):
        capture_ci(
            store,
            "oem-radar",
            mission=other["display_id"],
            inspect_remote=_remote_with_checks(),
        )
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_explicit_completed_mission_fails(tmp_path: Path) -> None:
    db = tmp_path / "explicit-done.db"
    store = open_store(db, actor="cursor")
    done = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    store.complete_mission(done["display_id"])
    store.start_mission("oem-radar", "still open")
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="COMPLETED"):
        capture_ci(
            store,
            "oem-radar",
            mission=done["display_id"],
            inspect_remote=_remote_with_checks(),
        )
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_reconcile_does_not_attach_ci_artefacts(tmp_path: Path) -> None:
    db = tmp_path / "ro.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    reconcile_clank(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert ledger_fingerprint(store) == before
    payload = dossier(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert payload["artifacts"] == []
    store.conn.close()


def test_terminal_shows_ci_artefacts(tmp_path: Path) -> None:
    db = tmp_path / "term.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
    html = _dossier_html(dossier(store, "oem-radar", inspect_remote=_remote_with_checks()))
    assert "ARTEFACTS" in html
    assert "github_ci" in html
    assert "CI success" in html
    store.conn.close()


def _checks_payload(sha: str, *, state="success", extras=None) -> dict:
    return {
        "source": EventSource.GITHUB,
        "ok": True,
        "error": None,
        "repo": "anil-ganti-nbc/oem-radar",
        "sha": sha,
        "state": state,
        "runs": [
            {
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://example.test/ci",
            }
        ],
        "contexts": [],
        "checks_observed": True,
        "statuses_observed": True,
        "check_run_total": 1,
        "checks_complete": True,
        "status_total": 0,
        "statuses_complete": True,
        "combined_state": None,
        **(extras or {}),
    }


def test_ci_capture_explicit_mission_uses_that_mission_sha_not_local_or_other(
    tmp_path: Path,
) -> None:
    db = tmp_path / "bind-b.db"
    store = open_store(db, actor="cursor")
    path = str(tmp_path / "oem-radar")
    first = _seed(store, path=path, remotes=[CANON], branch="main", head=HEAD)
    second = store.start_mission("oem-radar", "feature b")
    cp = store.record_checkpoint(
        second["display_id"],
        branch="feature/b",
        head=OTHER,
        source=EventSource.AGENT_REPORT,
    )
    requested: list[tuple[str, str]] = []

    def inspect_checks(repo, sha):
        requested.append((repo, sha))
        return _checks_payload(sha)

    result = capture_ci(
        store,
        "oem-radar",
        mission=second["display_id"],
        inspect_local=lambda _path: _local("main", HEAD),
        inspect_remote=_github(),
        inspect_checks=inspect_checks,
    )
    assert first["display_id"] != second["display_id"]
    assert requested == [("anil-ganti-nbc/oem-radar", OTHER)]
    assert result["mission_display"] == second["display_id"]
    assert result["sha"] == OTHER
    meta = _meta(result["artifact"])
    assert meta["mission_id"] == second["mission_id"]
    assert meta["mission_display"] == second["display_id"]
    assert meta["checkpoint_id"] == cp["checkpoint_id"]
    assert meta["recorded_branch"] == "feature/b"
    assert meta["recorded_sha"] == OTHER
    assert meta["sha"] == OTHER
    assert meta["sha_attribution"] == "mission_checkpoint"
    rec_after = reconcile_clank(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD),
        inspect_remote=_github(),
        mission=second["display_id"],
        include_ci=False,
    )
    assert rec_after["recorded"]["head"] == OTHER
    store.conn.close()


def test_ci_capture_unique_mission_uses_recorded_sha_not_unrelated_local(
    tmp_path: Path,
) -> None:
    db = tmp_path / "bind-unique.db"
    store = open_store(db, actor="cursor")
    mission = _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="feature/b",
        head=OTHER,
    )
    requested: list[str] = []

    def inspect_checks(repo, sha):
        requested.append(sha)
        return _checks_payload(sha)

    result = capture_ci(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD),
        inspect_remote=_github(),
        inspect_checks=inspect_checks,
    )
    assert requested == [OTHER]
    assert result["mission_display"] == mission["display_id"]
    assert result["sha"] == OTHER
    meta = _meta(result["artifact"])
    assert meta["recorded_sha"] == OTHER
    assert meta["sha"] == OTHER
    assert meta["sha_attribution"] == "mission_checkpoint"
    store.conn.close()


def test_ci_capture_mission_without_attributable_sha_fails(tmp_path: Path) -> None:
    db = tmp_path / "no-sha.db"
    store = open_store(db, actor="cursor")
    mission = _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="feature/b",
    )
    before = ledger_fingerprint(store)
    with pytest.raises(ValidationError, match="no SHA attributable"):
        capture_ci(
            store,
            "oem-radar",
            mission=mission["display_id"],
            inspect_local=lambda _path: _local("main", HEAD),
            inspect_remote=_remote_with_checks(),
        )
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_capture_local_head_only_when_on_recorded_branch(tmp_path: Path) -> None:
    db = tmp_path / "local-ok.db"
    store = open_store(db, actor="cursor")
    mission = _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="feature/b",
    )
    requested: list[str] = []

    def inspect_checks(repo, sha):
        requested.append(sha)
        return _checks_payload(sha)

    result = capture_ci(
        store,
        "oem-radar",
        mission=mission["display_id"],
        inspect_local=lambda _path: _local("feature/b", OTHER),
        inspect_remote=_github(),
        inspect_checks=inspect_checks,
    )
    assert requested == [OTHER]
    meta = _meta(result["artifact"])
    assert meta["recorded_sha"] is None
    assert meta["recorded_branch"] == "feature/b"
    assert meta["sha"] == OTHER
    assert meta["sha_attribution"] == "local_head_on_recorded_branch"
    store.conn.close()


def test_reconcile_without_mission_is_unchanged_and_read_only(tmp_path: Path) -> None:
    db = tmp_path / "recon-default.db"
    store = open_store(db, actor="cursor")
    first = _seed(store, remotes=[CANON], branch="main", head=HEAD)
    second = store.start_mission("oem-radar", "feature b")
    store.record_checkpoint(
        second["display_id"],
        branch="feature/b",
        head=OTHER,
        source=EventSource.AGENT_REPORT,
    )
    store.pause_mission(second["display_id"])
    before = ledger_fingerprint(store)
    rec = reconcile_clank(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert ledger_fingerprint(store) == before
    assert rec["mission_display"] == first["display_id"]
    assert rec["recorded"]["head"] == HEAD
    assert rec["observed_github"]["checks"]["sha"] == HEAD
    store.conn.close()
