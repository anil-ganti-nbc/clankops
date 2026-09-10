"""Foundation 5: record observed GitHub CI as Mission artefacts."""

from __future__ import annotations

from pathlib import Path

from clankops.ci import CI_ARTIFACT_KIND, capture_ci
from clankops.enums import EventSource
from clankops.errors import ValidationError
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.reconcile import reconcile_clank
from clankops.store import open_store
from clankops.terminal import _dossier_html

from test_foundation3 import CANON, HEAD, _github, _seed

import pytest


def _remote_with_checks(*, state="success", runs=None, extras=None):
    def _inspect(repo):
        payload = _github()(repo)
        payload["checks"] = {
            "source": EventSource.GITHUB,
            "ok": True,
            "error": None,
            "sha": HEAD,
            "state": state,
            "runs": runs
            or [{"name": "test", "status": "completed", "conclusion": "success", "html_url": "https://example.test/ci"}],
            "check_run_total": 1,
            "checks_complete": True,
            "status_total": 0,
            "statuses_complete": True,
            **(extras or {}),
        }
        return payload

    return _inspect


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
    assert result["rewrote_history"] is False
    assert result["rewrote_git_claims"] is False
    assert result["ci_state"] == "success"
    assert result["sha"] == HEAD
    art = result["artifact"]
    assert art["kind"] == CI_ARTIFACT_KIND
    assert art["source"] == EventSource.CI
    assert art["ref"] == HEAD
    rec_after = reconcile_clank(store, "oem-radar", inspect_remote=_remote_with_checks())
    assert rec_after["status"] == "aligned"
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


def test_ci_capture_requires_unfinished_mission(tmp_path: Path) -> None:
    db = tmp_path / "none.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar", remotes=[CANON])
    with pytest.raises(ValidationError, match="no unfinished Mission"):
        capture_ci(store, "oem-radar", inspect_remote=_remote_with_checks())
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
