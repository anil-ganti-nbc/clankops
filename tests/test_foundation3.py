"""Foundation 3: live git/GitHub observation vs recorded claims."""

from __future__ import annotations

import json
from pathlib import Path

from clankops.cli import main
from clankops.enums import EventSource
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.reconcile import heads_match, reconcile_clank
from clankops.store import open_readonly_store, open_store
from clankops.terminal import dispatch


def _local(branch: str, head: str, *, dirty: bool = False) -> dict:
    return {
        "is_git": True,
        "git_error": None,
        "current_branch": branch,
        "head": head,
        "dirty": dirty,
        "dirty_count": 2 if dirty else 0,
        "ahead": 0,
        "behind": 0,
        "upstream": "origin/" + branch,
    }


def _github(*, default_branch="main", default_head="aaabbbbccccddddeeeeffff0000111122223333", prs=None):
    def _inspect(repo):
        return {
            "source": "GITHUB",
            "ok": True,
            "error": None,
            "repo": repo,
            "default_branch": default_branch,
            "default_branch_head": default_head,
            "open_prs": prs or [],
        }

    return _inspect


def test_heads_match_accepts_short_prefix() -> None:
    full = "d27a8e13d190d1e546b820737d18a77af74372fc"
    assert heads_match(full, full[:7]) is True
    assert heads_match(full, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef") is False
    assert heads_match(None, full) is None


def test_reconcile_reports_head_and_branch_drift_without_mutating(tmp_path: Path) -> None:
    db = tmp_path / "rec.db"
    store = open_store(db, actor="cursor")
    store.register_clank(
        "oem-radar",
        local_path=str(tmp_path / "oem-radar"),
        remotes=["https://github.com/anil-ganti-nbc/oem-radar.git"],
    )
    mission = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(
        mission["display_id"],
        branch="expansion-handheld-sixunited-m1",
        head="adb04cc0b3cfd4266e46bb41af0c22044584272d",
        working_tree="clean",
        source=EventSource.AGENT_REPORT,
        git_evidence={
            "source": EventSource.LOCAL_GIT,
            "branch": "expansion-handheld-sixunited-m1",
            "head": "adb04cc0b3cfd4266e46bb41af0c22044584272d",
            "working_tree": "clean",
        },
    )
    before = ledger_fingerprint(store)
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", "ffffffffffffffffffffffffffffffffffffffff", dirty=True),
        inspect_remote=_github(
            prs=[
                {
                    "number": 9,
                    "title": "handheld",
                    "head_ref": "expansion-handheld-sixunited-m1",
                    "head": "9999999999999999999999999999999999999999",
                    "url": "https://github.com/anil-ganti-nbc/oem-radar/pull/9",
                    "is_draft": False,
                    "state": "OPEN",
                }
            ]
        ),
    )
    fields = {item["field"] for item in report["drift"]}
    sources = {item["source"] for item in report["drift"]}
    assert report["status"] == "drift"
    assert report["rewrote_history"] is False
    assert "branch" in fields
    assert "head" in fields
    assert "working_tree" in fields
    assert "pr_head" in fields
    assert "LOCAL_GIT" in sources
    assert "GITHUB" in sources
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_reconcile_aligned_when_live_matches_record(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar", local_path=str(tmp_path / "oem-radar"))
    mission = store.start_mission("oem-radar", "handheld")
    head = "aaabbbbccccddddeeeeffff0000111122223333"
    store.record_checkpoint(
        mission["display_id"],
        branch="main",
        head=head,
        working_tree="clean",
    )
    report = reconcile_clank(
        store,
        "oem-radar",
        include_github=False,
        inspect_local=lambda _path: _local("main", head, dirty=False),
    )
    assert report["status"] == "aligned"
    assert report["drift"] == []
    assert report["observed_github"] is None
    store.conn.close()


def test_reconcile_does_not_flag_feature_branch_vs_github_default(tmp_path: Path) -> None:
    db = tmp_path / "feat.db"
    store = open_store(db, actor="cursor")
    store.register_clank(
        "oem-radar",
        local_path=str(tmp_path / "oem-radar"),
        remotes=["https://github.com/anil-ganti-nbc/oem-radar.git"],
    )
    mission = store.start_mission("oem-radar", "handheld")
    head = "1111111111111111111111111111111111111111"
    store.record_checkpoint(mission["display_id"], branch="feature/x", head=head, working_tree="clean")
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("feature/x", head),
        inspect_remote=_github(default_head="0000000000000000000000000000000000000000"),
    )
    assert report["status"] == "aligned"
    assert report["drift"] == []
    store.conn.close()


def test_cli_reconcile_is_read_only(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cli.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar")
    store.start_mission("oem-radar", "handheld")
    before = ledger_fingerprint(store)
    store.conn.close()
    assert main(["--db", str(db), "--json", "reconcile", "oem-radar", "--no-github"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rewrote_history"] is False
    assert payload["slug"] == "oem-radar"
    reader = open_readonly_store(db)
    assert ledger_fingerprint(reader) == before
    reader.conn.close()


def test_terminal_reconciliation_section_and_malformed_duration(tmp_path: Path) -> None:
    db = tmp_path / "term.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar", display_name="OEM Radar", local_path=str(tmp_path / "oem-radar"))
    mission = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(
        mission["display_id"],
        branch="old-branch",
        head="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        working_tree="clean",
    )
    before = ledger_fingerprint(store)
    payload = dossier(
        store,
        "oem-radar",
        include_github=False,
        inspect_local=lambda _path: _local("now-branch", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    )
    assert payload["reconcile"]["status"] == "drift"
    status, _, body = dispatch(store, "GET", "/clank/oem-radar?github=0")
    assert status == 200
    html = body.decode("utf-8")
    assert "<h2>RECONCILIATION</h2>" in html
    assert "LOCAL_GIT" in html
    assert "GITHUB" in html or "not requested" in html
    status, _, body = dispatch(store, "GET", "/?older-than=nope")
    assert status == 400
    assert b"duration" in body
    status, _, _ = dispatch(store, "POST", "/api/clank/oem-radar/reconcile")
    assert status == 405
    assert ledger_fingerprint(store) == before
    store.conn.close()
