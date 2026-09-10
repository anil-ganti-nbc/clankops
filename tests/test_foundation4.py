"""Foundation 4: GitHub commit-status / CI evidence, without rewriting history."""

from __future__ import annotations

import json
from pathlib import Path

from clankops.cli import main
from clankops.enums import EventSource
from clankops.githubinspect import inspect_commit_status, rollup_ci_state
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.reconcile import reconcile_clank
from clankops.store import open_store
from clankops.terminal import _dossier_html

from test_foundation3 import CANON, HEAD, OTHER, _github, _local, _seed

ROOT = Path(__file__).resolve().parents[1]


def test_empty_checks_are_none_never_success() -> None:
    assert rollup_ci_state([], contexts=[]) == "none"
    assert (
        rollup_ci_state(
            [],
            contexts=[],
            checks_observed=True,
            statuses_observed=True,
        )
        == "none"
    )
    assert rollup_ci_state([], contexts=[{"state": "success"}, {"state": "success"}]) == "success"
    assert rollup_ci_state([], contexts=[{"state": "pending"}]) == "pending"
    assert rollup_ci_state([], contexts=[{"state": "failure"}]) == "failure"


def test_unavailable_status_evidence_is_unknown_not_none() -> None:
    assert (
        rollup_ci_state(
            [],
            contexts=[],
            checks_observed=True,
            statuses_observed=False,
        )
        == "unknown"
    )
    assert (
        rollup_ci_state(
            [],
            contexts=[],
            checks_observed=False,
            statuses_observed=True,
        )
        == "unknown"
    )
    assert (
        rollup_ci_state(
            [],
            contexts=[],
            checks_observed=False,
            statuses_observed=False,
        )
        == "unknown"
    )


def test_unified_check_run_and_status_context_rollup() -> None:
    success_run = {"status": "completed", "conclusion": "success"}
    assert (
        rollup_ci_state(
            [success_run],
            contexts=[{"context": "legacy", "state": "failure"}],
        )
        == "failure"
    )
    assert (
        rollup_ci_state(
            [success_run],
            contexts=[{"context": "legacy", "state": "pending"}],
        )
        == "pending"
    )
    assert (
        rollup_ci_state(
            [success_run],
            contexts=[{"context": "legacy", "state": "success"}],
        )
        == "success"
    )


def test_check_run_failure_beats_pending_and_success() -> None:
    runs = [
        {"status": "completed", "conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
        {"status": "completed", "conclusion": "failure"},
    ]
    assert rollup_ci_state(runs, contexts=[{"state": "success"}]) == "failure"


def test_check_run_completeness_before_success() -> None:
    incomplete = {"status": "in_progress", "conclusion": "success"}
    missing_status = {"status": None, "conclusion": "success"}
    completed_no_conclusion = {"status": "completed", "conclusion": None}
    assert rollup_ci_state([incomplete], contexts=[]) == "pending"
    assert rollup_ci_state([missing_status], contexts=[]) == "pending"
    assert rollup_ci_state([completed_no_conclusion], contexts=[]) == "unknown"
    assert (
        rollup_ci_state(
            [{"status": "completed", "conclusion": "success"}],
            contexts=[],
        )
        == "success"
    )


def test_truncated_check_run_page_is_not_success() -> None:
    one_ok = [{"status": "completed", "conclusion": "success"}]
    assert rollup_ci_state(one_ok, contexts=[], check_run_total=2) != "success"
    assert rollup_ci_state(one_ok, contexts=[], check_run_total=2) == "unknown"


def test_complete_successful_check_run_set_is_success() -> None:
    two_ok = [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "success"},
    ]
    assert rollup_ci_state(two_ok, contexts=[], check_run_total=2) == "success"


def test_incomplete_page_with_visible_failure_is_failure() -> None:
    runs = [{"status": "completed", "conclusion": "failure"}]
    assert rollup_ci_state(runs, contexts=[], check_run_total=2) == "failure"


def test_zero_runs_and_empty_status_plane_is_none() -> None:
    assert (
        rollup_ci_state(
            [],
            contexts=[],
            check_run_total=0,
            checks_observed=True,
            statuses_observed=True,
        )
        == "none"
    )


def test_single_successful_run_matching_total_count_is_success() -> None:
    runs = [{"status": "completed", "conclusion": "success"}]
    assert rollup_ci_state(runs, contexts=[], check_run_total=1) == "success"


def test_inspect_preserves_total_count_and_paginates(monkeypatch) -> None:
    pages: list[str] = []
    run_ok = {
        "id": 1,
        "name": "test",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://example.test/1",
    }
    run_ok2 = {**run_ok, "id": 2, "name": "lint"}

    def fake_gh(args):
        path = str(args[1]) if len(args) > 1 else ""
        if "check-runs" in path:
            pages.append(path)
            if "page=2" in path:
                return 0, json.dumps({"total_count": 2, "check_runs": [run_ok2]}), ""
            return 0, json.dumps({"total_count": 2, "check_runs": [run_ok]}), ""
        return 0, json.dumps({"state": "pending", "total_count": 0, "statuses": []}), ""

    monkeypatch.setattr("clankops.githubinspect.run_gh", fake_gh)
    result = inspect_commit_status("anil-ganti-nbc/clankops", HEAD)
    assert result["check_run_total"] == 2
    assert len(result["runs"]) == 2
    assert result["checks_complete"] is True
    assert result["state"] == "success"
    assert any("per_page=100" in item for item in pages)
    assert any("page=2" in item for item in pages)


def test_inspect_truncated_check_runs_refuse_success(monkeypatch) -> None:
    run_ok = {
        "id": 1,
        "name": "test",
        "status": "completed",
        "conclusion": "success",
    }

    def fake_gh(args):
        path = str(args[1]) if len(args) > 1 else ""
        if "check-runs" in path:
            return 0, json.dumps({"total_count": 2, "check_runs": [run_ok]}), ""
        return 0, json.dumps({"state": "pending", "total_count": 0, "statuses": []}), ""

    monkeypatch.setattr("clankops.githubinspect.run_gh", fake_gh)
    result = inspect_commit_status("anil-ganti-nbc/clankops", HEAD)
    assert result["check_run_total"] == 2
    assert len(result["runs"]) == 1
    assert result["checks_complete"] is False
    assert result["state"] == "unknown"
    assert result["state"] != "success"


def test_inspect_commit_status_is_get_only(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_gh(args):
        calls.append(args)
        path = str(args[1]) if len(args) > 1 else ""
        if "check-runs" in path:
            return 0, json.dumps({"total_count": 0, "check_runs": []}), ""
        if path.endswith("/status"):
            return 0, json.dumps({"state": "pending", "total_count": 0, "statuses": []}), ""
        return 1, "", "unexpected"

    monkeypatch.setattr("clankops.githubinspect.run_gh", fake_gh)
    result = inspect_commit_status("anil-ganti-nbc/clankops", HEAD)
    assert result["ok"] is True
    assert result["state"] == "none"
    assert result["checks_observed"] is True
    assert result["statuses_observed"] is True
    assert result["check_run_total"] == 0
    assert result["checks_complete"] is True
    assert result["combined_state"] == "pending"
    assert result["combined_total"] == 0
    assert all(args[0] == "api" for args in calls)
    assert not any("pr" in args for args in calls)
    assert any("per_page=100" in str(args[1]) for args in calls if len(args) > 1)


def test_inspect_commit_status_unavailable_status_is_not_none(monkeypatch) -> None:
    def fake_gh(args):
        path = str(args[1]) if len(args) > 1 else ""
        if "check-runs" in path:
            return 0, json.dumps({"total_count": 0, "check_runs": []}), ""
        return 1, "", "gh api commit status failed (1)"

    monkeypatch.setattr("clankops.githubinspect.run_gh", fake_gh)
    result = inspect_commit_status("anil-ganti-nbc/clankops", HEAD)
    assert result["ok"] is True
    assert result["checks_observed"] is True
    assert result["statuses_observed"] is False
    assert result["state"] == "unknown"
    assert result["state"] != "none"


def test_injected_remote_does_not_call_live_checks(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "no-live.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    called: list[tuple] = []
    monkeypatch.setattr(
        "clankops.reconcile.inspect_commit_status",
        lambda repo, sha: called.append((repo, sha)) or {"state": "success"},
    )
    report = reconcile_clank(store, "oem-radar", inspect_remote=_github())
    assert called == []
    assert report["observed_github"]["checks"]["error"] == "not requested"
    assert report["observed_github"]["checks"]["state"] is None
    assert report["status"] == "aligned"
    store.conn.close()


def test_default_path_observes_checks_for_local_head(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "live-path.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    seen: list[tuple] = []
    monkeypatch.setattr("clankops.reconcile.inspect_github", lambda repo: _github()(repo))
    monkeypatch.setattr(
        "clankops.reconcile.inspect_commit_status",
        lambda repo, sha: seen.append((repo, sha))
        or {
            "source": EventSource.GITHUB,
            "ok": True,
            "error": None,
            "repo": repo,
            "sha": sha,
            "state": "none",
            "runs": [],
        },
    )
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", HEAD),
    )
    assert seen == [("anil-ganti-nbc/oem-radar", HEAD)]
    assert report["observed_github"]["checks"]["state"] == "none"
    assert report["status"] == "aligned"
    store.conn.close()


def test_check_sha_falls_back_to_recorded_then_default_head(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "sha.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    seen: list[str | None] = []
    monkeypatch.setattr(
        "clankops.reconcile.inspect_github",
        lambda repo: _github(default_head=OTHER)(repo),
    )
    monkeypatch.setattr(
        "clankops.reconcile.inspect_commit_status",
        lambda repo, sha: seen.append(sha) or {"ok": True, "state": "none", "sha": sha, "runs": []},
    )
    reconcile_clank(store, "oem-radar")
    assert seen == [HEAD]

    seen.clear()
    store2 = open_store(tmp_path / "sha2.db", actor="cursor")
    store2.register_clank("oem-radar", remotes=[CANON])
    store2.start_mission("oem-radar", "handheld")
    monkeypatch.setattr(
        "clankops.reconcile.inspect_github",
        lambda repo: _github(default_head=OTHER)(repo),
    )
    reconcile_clank(store2, "oem-radar")
    assert seen == [OTHER]
    store.conn.close()
    store2.conn.close()


def test_ci_failure_does_not_create_git_drift(tmp_path: Path) -> None:
    db = tmp_path / "ci-fail.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)

    def remote(repo):
        payload = _github()(repo)
        payload["checks"] = {
            "source": EventSource.GITHUB,
            "ok": True,
            "error": None,
            "sha": HEAD,
            "state": "failure",
            "runs": [{"name": "pytest", "status": "completed", "conclusion": "failure"}],
        }
        return payload

    report = reconcile_clank(store, "oem-radar", inspect_remote=remote)
    assert report["status"] == "aligned"
    assert report["drift"] == []
    assert report["observed_github"]["checks"]["state"] == "failure"
    assert report["rewrote_history"] is False
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_ci_success_does_not_turn_unknown_git_into_aligned(tmp_path: Path) -> None:
    db = tmp_path / "ci-ok.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="feature/x", head=HEAD, working_tree="clean")

    def remote(repo):
        payload = _github(prs=[])(repo)
        payload["checks"] = {
            "ok": True,
            "state": "success",
            "sha": HEAD,
            "runs": [{"name": "pytest", "status": "completed", "conclusion": "success"}],
        }
        return payload

    report = reconcile_clank(store, "oem-radar", inspect_remote=remote)
    assert report["observed_github"]["checks"]["state"] == "success"
    assert report["status"] == "unknown"
    assert report["status"] != "aligned"
    store.conn.close()


def test_terminal_shows_ci_checks_without_claiming_git_match(tmp_path: Path) -> None:
    db = tmp_path / "term.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)

    def remote(repo):
        payload = _github()(repo)
        payload["checks"] = {
            "source": EventSource.GITHUB,
            "ok": True,
            "error": None,
            "sha": HEAD,
            "state": "none",
            "runs": [],
        }
        return payload

    payload = dossier(store, "oem-radar", inspect_remote=remote)
    html = _dossier_html(payload)
    assert "CI checks" in html
    assert "no check-runs" in html
    assert "no status contexts" in html
    assert "Empty CI is none only when check-runs and status contexts were both observed empty" in html
    assert "matches recorded claims" not in html
    store.conn.close()


def test_pytest_workflow_is_pr_and_main_push_only() -> None:
    text = (ROOT / ".github" / "workflows" / "pytest.yml").read_text(encoding="utf-8")
    assert "python -m pytest" in text
    assert 'python-version: "3.14"' in text
    assert "pull_request" in text
    assert "branches: [main]" in text
    assert "schedule:" not in text
    assert "cron" not in text.lower()
    assert "workflow_dispatch" not in text


def test_cli_reconcile_still_read_only_with_ci_field(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar")
    store.start_mission("oem-radar", "handheld")
    before = ledger_fingerprint(store)
    store.conn.close()
    assert main(["--db", str(db), "reconcile", "oem-radar", "--no-github"]) == 0
    out = capsys.readouterr().out
    assert "CI=unknown" in out
    reader_db = open_store(db, actor="cursor")
    assert ledger_fingerprint(reader_db) == before
    reader_db.conn.close()
