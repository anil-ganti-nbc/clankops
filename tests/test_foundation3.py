"""Foundation 3: live git/GitHub observation vs recorded claims."""

from __future__ import annotations

import json
from pathlib import Path

from clankops.cli import main
from clankops.enums import EventSource
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.reconcile import github_repo_choice, heads_match, reconcile_clank
from clankops.store import open_readonly_store, open_store
from clankops.terminal import _dossier_html, dispatch

HEAD = "aaabbbbccccddddeeeeffff0000111122223333"
OTHER = "ffffffffffffffffffffffffffffffffffffffff"
CANON = "https://github.com/anil-ganti-nbc/oem-radar.git"
FORK = "https://github.com/example-fork/oem-radar.git"
OTHER_FORK = "https://github.com/other-org/oem-radar.git"


def _local(branch: str, head: str, *, dirty: bool | None = False) -> dict:
    return {
        "is_git": True,
        "git_error": None,
        "current_branch": branch,
        "head": head,
        "dirty": dirty,
        "dirty_count": 2 if dirty else 0,
        "ahead": 0,
        "behind": 0,
        "upstream": None if branch == "HEAD" else "origin/" + branch,
    }


def _github(*, default_branch="main", default_head=HEAD, prs=None, ok=True, repo=None):
    def _inspect(chosen):
        return {
            "source": "GITHUB",
            "ok": ok,
            "error": None if ok else "forced failure",
            "repo": chosen if repo is None else repo,
            "default_branch": default_branch,
            "default_branch_head": default_head,
            "open_prs": prs or [],
        }

    return _inspect


def _seed(store, *, path=None, remotes=None, branch=None, head=None, working_tree=None):
    store.register_clank(
        "oem-radar",
        local_path=path,
        remotes=remotes or [],
    )
    mission = store.start_mission("oem-radar", "handheld")
    if branch or head or working_tree:
        store.record_checkpoint(
            mission["display_id"],
            branch=branch,
            head=head,
            working_tree=working_tree,
            source=EventSource.AGENT_REPORT,
        )
    return mission


def test_heads_match_accepts_short_prefix() -> None:
    full = "d27a8e13d190d1e546b820737d18a77af74372fc"
    assert heads_match(full, full[:7]) is True
    assert heads_match(full, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef") is False
    assert heads_match(None, full) is None


def test_github_only_default_branch_matching_head_is_aligned(tmp_path: Path) -> None:
    db = tmp_path / "gh-ok.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    called: list[str] = []

    def remote(repo):
        called.append(repo)
        return _github()(repo)

    report = reconcile_clank(store, "oem-radar", inspect_remote=remote)
    assert report["status"] == "aligned"
    assert report["comparisons"]["branch"]["status"] == "corroborated"
    assert report["comparisons"]["branch"]["source"] == "GITHUB"
    assert report["comparisons"]["head"]["status"] == "corroborated"
    assert report["comparisons"]["head"]["source"] == "GITHUB"
    assert report["comparisons"]["working_tree"]["status"] == "not_recorded"
    assert called == ["anil-ganti-nbc/oem-radar"]
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_github_only_default_branch_mismatching_head_is_drift(tmp_path: Path) -> None:
    db = tmp_path / "gh-bad.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_remote=_github(default_head=OTHER),
    )
    assert report["status"] == "drift"
    assert report["comparisons"]["head"]["status"] == "contradicted"
    assert report["comparisons"]["head"]["source"] == "GITHUB"
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_github_only_feature_branch_without_pr_is_not_aligned(tmp_path: Path) -> None:
    db = tmp_path / "gh-feat.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="feature/x", head=HEAD)
    report = reconcile_clank(store, "oem-radar", inspect_remote=_github(prs=[]))
    assert report["status"] != "aligned"
    assert report["status"] == "unknown"
    assert report["comparisons"]["branch"]["status"] == "unobservable"
    assert report["comparisons"]["head"]["status"] == "unobservable"


def test_github_ok_without_comparable_evidence_is_not_aligned(tmp_path: Path) -> None:
    db = tmp_path / "gh-meta.db"
    store = open_store(db, actor="cursor")
    _seed(store, remotes=[CANON], branch="feature/x", head=HEAD, working_tree="clean")
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_remote=_github(ok=True, default_branch="main", default_head=OTHER, prs=[]),
    )
    assert report["observed_github"]["ok"] is True
    assert report["status"] != "aligned"
    assert report["status"] == "unknown"


def test_detached_head_matching_sha_is_partial_not_aligned(tmp_path: Path) -> None:
    db = tmp_path / "detach.db"
    store = open_store(db, actor="cursor")
    _seed(store, path=str(tmp_path / "oem-radar"), branch="main", head=HEAD)
    before = ledger_fingerprint(store)
    report = reconcile_clank(
        store,
        "oem-radar",
        include_github=False,
        inspect_local=lambda _path: _local("HEAD", HEAD, dirty=False),
    )
    assert report["status"] == "partial"
    assert report["status"] != "aligned"
    assert report["comparisons"]["head"]["status"] == "corroborated"
    assert report["comparisons"]["branch"]["status"] == "unobservable"
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_unobservable_working_tree_is_partial(tmp_path: Path) -> None:
    db = tmp_path / "tree.db"
    store = open_store(db, actor="cursor")
    _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        branch="main",
        head=HEAD,
        working_tree="clean",
    )
    report = reconcile_clank(
        store,
        "oem-radar",
        include_github=False,
        inspect_local=lambda _path: _local("main", HEAD, dirty=None),
    )
    assert report["status"] == "partial"
    assert report["comparisons"]["branch"]["status"] == "corroborated"
    assert report["comparisons"]["head"]["status"] == "corroborated"
    assert report["comparisons"]["working_tree"]["status"] == "unobservable"


def test_every_recorded_field_matching_is_aligned(tmp_path: Path) -> None:
    db = tmp_path / "all.db"
    store = open_store(db, actor="cursor")
    _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        branch="main",
        head=HEAD,
        working_tree="clean",
    )
    report = reconcile_clank(
        store,
        "oem-radar",
        include_github=False,
        inspect_local=lambda _path: _local("main", HEAD, dirty=False),
    )
    assert report["status"] == "aligned"
    assert report["comparisons"]["branch"]["status"] == "corroborated"
    assert report["comparisons"]["head"]["status"] == "corroborated"
    assert report["comparisons"]["working_tree"]["status"] == "corroborated"


def test_any_compared_mismatch_is_drift(tmp_path: Path) -> None:
    db = tmp_path / "mis.db"
    store = open_store(db, actor="cursor")
    _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="expansion-handheld-sixunited-m1",
        head=HEAD,
        working_tree="clean",
    )
    before = ledger_fingerprint(store)
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("main", OTHER, dirty=True),
        inspect_remote=_github(
            prs=[
                {
                    "number": 9,
                    "title": "handheld",
                    "head_ref": "expansion-handheld-sixunited-m1",
                    "head": OTHER,
                    "url": "https://github.com/anil-ganti-nbc/oem-radar/pull/9",
                    "is_draft": False,
                    "state": "OPEN",
                }
            ]
        ),
    )
    assert report["status"] == "drift"
    assert report["rewrote_history"] is False
    assert report["comparisons"]["branch"]["status"] == "contradicted"
    assert report["comparisons"]["head"]["status"] == "contradicted"
    assert report["comparisons"]["working_tree"]["status"] == "contradicted"
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_no_recorded_claims_is_no_record(tmp_path: Path) -> None:
    db = tmp_path / "none.db"
    store = open_store(db, actor="cursor")
    _seed(store, path=str(tmp_path / "oem-radar"))
    before = ledger_fingerprint(store)
    report = reconcile_clank(
        store,
        "oem-radar",
        include_github=False,
        inspect_local=lambda _path: _local("main", HEAD),
    )
    assert report["status"] == "no-record"
    assert report["comparisons"]["branch"]["status"] == "not_recorded"
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_neither_observer_can_verify_existing_claims_is_unknown(tmp_path: Path) -> None:
    db = tmp_path / "unk.db"
    store = open_store(db, actor="cursor")
    _seed(store, branch="main", head=HEAD, working_tree="clean")
    report = reconcile_clank(store, "oem-radar", include_github=False)
    assert report["status"] == "unknown"
    assert report["status"] != "aligned"
    assert report["comparisons"]["branch"]["status"] == "unobservable"
    assert report["comparisons"]["head"]["status"] == "unobservable"
    assert report["comparisons"]["working_tree"]["status"] == "unobservable"


def test_terminal_wording_never_claims_match_for_incomplete_statuses(tmp_path: Path) -> None:
    db = tmp_path / "term.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar", display_name="OEM Radar")
    mission = store.start_mission("oem-radar", "handheld")
    none = dossier(store, "oem-radar", include_github=False)
    assert none["reconcile"]["status"] == "no-record"
    html_none = _dossier_html(none)
    assert "matches recorded claims" not in html_none
    assert "no recorded Git state to reconcile" in html_none

    store.record_checkpoint(mission["display_id"], branch="main", head=HEAD)
    unknown = dossier(store, "oem-radar", include_github=False)
    assert unknown["reconcile"]["status"] == "unknown"
    html_unknown = _dossier_html(unknown)
    assert "matches recorded claims" not in html_unknown
    assert "could not be independently verified" in html_unknown

    store.register_clank("watch-clank", local_path=str(tmp_path / "watch"))
    wmission = store.start_mission("watch-clank", "health")
    store.record_checkpoint(wmission["display_id"], branch="main", head=HEAD, working_tree="clean")
    partial = dossier(
        store,
        "watch-clank",
        include_github=False,
        inspect_local=lambda _path: _local("main", HEAD, dirty=None),
    )
    assert partial["reconcile"]["status"] == "partial"
    html_partial = _dossier_html(partial)
    assert "matches recorded claims" not in html_partial
    assert "some recorded claims corroborated" in html_partial
    assert "LOCAL_GIT" in html_partial
    before = ledger_fingerprint(store)
    status, _, body = dispatch(store, "GET", "/?older-than=nope")
    assert status == 400
    status, _, _ = dispatch(store, "POST", "/api/clank/oem-radar/reconcile")
    assert status == 405
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_canonical_github_ref_beats_fork_regardless_of_insertion_order(tmp_path: Path) -> None:
    for first, second, first_canon, second_canon in (
        ("git_remote", "github_repo", False, True),
        ("github_repo", "git_remote", True, False),
    ):
        db = tmp_path / f"order-{first}.db"
        store = open_store(db, actor="cursor")
        store.register_clank("oem-radar")
        if first == "git_remote":
            store.update_ref("oem-radar", "git_remote", FORK, canonical=first_canon)
            store.update_ref("oem-radar", "github_repo", CANON, canonical=second_canon)
        else:
            store.update_ref("oem-radar", "github_repo", CANON, canonical=first_canon)
            store.update_ref("oem-radar", "git_remote", FORK, canonical=second_canon)
        choice = github_repo_choice(store.clank_detail("oem-radar")["refs"])
        assert choice["repo"] == "anil-ganti-nbc/oem-radar"
        called: list[str] = []
        store.record_checkpoint(
            store.start_mission("oem-radar", "handheld")["display_id"],
            branch="main",
            head=HEAD,
        )
        reconcile_clank(
            store,
            "oem-radar",
            inspect_remote=lambda repo: called.append(repo) or _github()(repo),
        )
        assert called == ["anil-ganti-nbc/oem-radar"]
        store.conn.close()


def test_ambiguous_noncanonical_github_refs_are_not_guessed(tmp_path: Path) -> None:
    db = tmp_path / "amb.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar")
    store.update_ref("oem-radar", "git_remote", FORK, canonical=False)
    store.update_ref("oem-radar", "git_remote", OTHER_FORK, canonical=False)
    choice = github_repo_choice(store.clank_detail("oem-radar")["refs"])
    assert choice["repo"] is None
    assert choice["ambiguous"] is True
    called: list[str] = []
    store.record_checkpoint(
        store.start_mission("oem-radar", "handheld")["display_id"],
        branch="main",
        head=HEAD,
    )
    before = ledger_fingerprint(store)
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_remote=lambda repo: called.append(repo) or _github()(repo),
    )
    assert called == []
    assert report["status"] == "unknown"
    assert report["observed_github"]["ambiguous"] is True
    assert "example-fork/oem-radar" in report["github_selection"]["error"]
    assert "other-org/oem-radar" in report["github_selection"]["error"]
    assert ledger_fingerprint(store) == before
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
    assert payload["status"] == "no-record"
    reader = open_readonly_store(db)
    assert ledger_fingerprint(reader) == before
    reader.conn.close()


def test_feature_branch_matching_locally_is_not_drift_vs_github_default(tmp_path: Path) -> None:
    db = tmp_path / "feat.db"
    store = open_store(db, actor="cursor")
    _seed(
        store,
        path=str(tmp_path / "oem-radar"),
        remotes=[CANON],
        branch="feature/x",
        head=HEAD,
        working_tree="clean",
    )
    report = reconcile_clank(
        store,
        "oem-radar",
        inspect_local=lambda _path: _local("feature/x", HEAD),
        inspect_remote=_github(default_head=OTHER),
    )
    assert report["status"] == "aligned"
    assert report["drift"] == []
    store.conn.close()
