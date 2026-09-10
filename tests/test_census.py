"""Census parser, dirty-repo handling, ambiguous candidates, duplicates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from clankops.census import (
    classify_candidate,
    detect_duplicates,
    inspect_path,
    load_census,
    path_leaf,
    run_census,
    write_census,
)
from clankops.cli import main
from clankops.enums import EventSource
from clankops.gitinspect import inspect_git
from clankops.store import open_store


def _run_git(path: Path, args: list[str]) -> None:
    subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_init(path: Path, *, dirty: bool = False, remote: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, ["init", "-b", "main"])
    _run_git(path, ["config", "user.email", "census@example.test"])
    _run_git(path, ["config", "user.name", "Census"])
    (path / "README.md").write_text("# OEM Radar\n\nProduct-intelligence platform.\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname='oem-radar'\n", encoding="utf-8")
    _run_git(path, ["add", "README.md", "pyproject.toml"])
    _run_git(path, ["commit", "-m", "init"])
    if remote:
        _run_git(path, ["remote", "add", "origin", remote])
    if dirty:
        (path / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")


def test_dirty_repository_is_recorded_not_cleaned(tmp_path: Path) -> None:
    repo = tmp_path / "oem-radar"
    _git_init(repo, dirty=True, remote="https://github.com/anil-ganti-nbc/oem-radar.git")
    git = inspect_git(repo)
    assert git["is_git"] is True
    assert git["dirty"] is True
    assert git["dirty_count"] >= 1
    assert any("scratch.txt" in line for line in git["dirty_sample"])
    assert (repo / "scratch.txt").read_text(encoding="utf-8") == "uncommitted\n"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    assert "scratch.txt" in status.stdout


def test_ambiguous_candidate_is_unknown(tmp_path: Path) -> None:
    mystery = tmp_path / "random-folder"
    mystery.mkdir()
    cand = inspect_path(mystery, group="other")
    assert cand["classification"] == "UNKNOWN"
    assert cand["confidence"] == "low"


def test_verified_fleet_repo(tmp_path: Path) -> None:
    repo = tmp_path / "oem-radar"
    _git_init(repo, remote="https://github.com/anil-ganti-nbc/oem-radar.git")
    cand = inspect_path(repo, group="clanks_root")
    assert cand["classification"] == "VERIFIED"
    assert cand["canonical_remote"].endswith("oem-radar.git")
    assert "git remote origin" in " ".join(cand["evidence"]) or any(
        "anil-ganti-nbc" in e for e in cand["evidence"]
    )


def test_duplicate_detection() -> None:
    candidates = [
        {
            "slug": "oem-radar",
            "local_path": r"C:\Users\anil\Clanks\oem-radar",
            "canonical_remote": "https://github.com/anil-ganti-nbc/oem-radar.git",
        },
        {
            "slug": "oem-radar",
            "local_path": r"C:\Users\anil\Documents\Default Project\oem-radar",
            "canonical_remote": "https://github.com/anil-ganti-nbc/oem-radar.git",
        },
    ]
    dupes = detect_duplicates(candidates)
    assert len(dupes) == 1
    assert len(dupes[0]["paths"]) == 2


def test_path_leaf_is_host_independent() -> None:
    assert path_leaf(r"C:\Users\anil\Clanks\_Launchers") == "_Launchers"
    assert path_leaf("C:/Users/anil/Clanks/_Launchers") == "_Launchers"
    assert path_leaf("/mnt/c/Users/anil/Clanks/_Launchers") == "_Launchers"
    assert path_leaf(r"C:\x\ml-lab") == "ml-lab"


def test_classify_support_and_not_a_clank() -> None:
    cls, evidence, _ = classify_candidate(
        {"slug": "_launchers", "local_path": r"C:\Users\anil\Clanks\_Launchers", "discovery_group": "clanks_root"}
    )
    assert cls == "SUPPORT_COMPONENT"
    cls, _, _ = classify_candidate(
        {
            "slug": "_launchers",
            "local_path": "/mnt/c/Users/anil/Clanks/_Launchers",
            "discovery_group": "clanks_root",
        }
    )
    assert cls == "SUPPORT_COMPONENT"
    cls, _, _ = classify_candidate({"slug": "ml-lab", "local_path": r"C:\x\ml-lab"})
    assert cls == "NOT_A_CLANK"
    cls, _, _ = classify_candidate(
        {
            "slug": "token-stats",
            "local_path": r"C:\Users\anil\Desktop\Quartermaster Clank\token-stats",
            "canonical_remote": "https://github.com/Annihilater/token-stats.git",
        }
    )
    assert cls == "SUPPORT_COMPONENT"


def test_census_roundtrip_and_parser(tmp_path: Path) -> None:
    root = tmp_path / "Clanks"
    _git_init(
        root / "oem-radar",
        remote="https://github.com/anil-ganti-nbc/oem-radar.git",
        dirty=True,
    )
    (root / "_Launchers").mkdir()
    mystery = root / "untitled"
    mystery.mkdir()
    census = run_census(roots=[root], include_github=False)
    assert census["census_version"] == 1
    assert "candidates" in census
    out = tmp_path / "clank_census.json"
    write_census(census, out)
    loaded = load_census(out)
    assert loaded["counts"]["total_candidates"] == census["counts"]["total_candidates"]
    slugs = {c["slug"] for c in loaded["candidates"]}
    assert "oem-radar" in slugs
    oem = next(c for c in loaded["candidates"] if c["slug"] == "oem-radar")
    assert oem["dirty"] is True
    launchers = next(c for c in loaded["candidates"] if c["slug"] in {"_launchers", "launchers"} or "Launchers" in (c.get("local_path") or ""))
    assert launchers["classification"] == "SUPPORT_COMPONENT"


def test_census_import_reconstructed(tmp_path: Path) -> None:
    census = {
        "candidates": [
            {
                "name": "oem-radar",
                "slug": "oem-radar",
                "display_name": "OEM Radar",
                "aliases": ["radar"],
                "local_path": r"C:\Users\anil\Clanks\oem-radar",
                "remote": "https://github.com/anil-ganti-nbc/oem-radar.git",
                "canonical_remote": "https://github.com/anil-ganti-nbc/oem-radar.git",
                "classification": "VERIFIED",
                "apparent_purpose": "OEM Radar",
                "evidence": ["git remote origin"],
            },
            {
                "name": "mystery",
                "slug": "mystery",
                "classification": "UNKNOWN",
                "local_path": r"C:\tmp\mystery",
            },
        ]
    }
    store = open_store(tmp_path / "db.db")
    stats = store.import_census(census)
    assert stats["registered"] == 1
    assert stats["candidates"] == 2
    clank = store.resolve_clank("oem-radar")
    events = store.history("oem-radar")
    assert events[0].source == EventSource.RECONSTRUCTED
    assert clank["provenance_source"] == "RECONSTRUCTED"
    # UNKNOWN is a candidate, not a Clank
    candidates = store.conn.execute("SELECT classification FROM census_candidates").fetchall()
    classes = {r[0] for r in candidates}
    assert "UNKNOWN" in classes
    store.conn.close()


def test_secondary_checkout_vs_sole_copy(tmp_path: Path) -> None:
    clanks = tmp_path / "Clanks"
    docs = tmp_path / "Default Project"
    _git_init(
        clanks / "oem-radar",
        remote="https://github.com/anil-ganti-nbc/oem-radar.git",
    )
    _git_init(
        docs / "oem-radar",
        remote="https://github.com/anil-ganti-nbc/oem-radar.git",
    )
    _git_init(
        docs / "motherclank",
        remote="https://github.com/anil-ganti-nbc/motherclank.git",
    )
    census = run_census(roots=[clanks, docs], include_github=False)
    by_path = {c["local_path"]: c for c in census["candidates"] if c.get("local_path")}
    assert by_path[str(clanks / "oem-radar")]["classification"] == "VERIFIED"
    assert by_path[str(docs / "oem-radar")]["classification"] == "SUPPORT_COMPONENT"
    assert by_path[str(docs / "motherclank")]["classification"] == "VERIFIED"


def test_duplicate_clank_detection_on_import(tmp_path: Path) -> None:
    store = open_store(tmp_path / "db.db")
    store.register_clank(
        "oem-radar",
        remotes=["https://github.com/anil-ganti-nbc/oem-radar.git"],
        source=EventSource.RECONSTRUCTED,
        actor="system",
    )
    census = {
        "candidates": [
            {
                "slug": "oem-radar",
                "name": "oem-radar",
                "local_path": r"C:\Users\anil\Documents\Default Project\oem-radar",
                "canonical_remote": "https://github.com/anil-ganti-nbc/oem-radar.git",
                "classification": "VERIFIED",
            }
        ]
    }
    stats = store.import_census(census)
    assert stats["skipped_duplicates"] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM clanks").fetchone()[0] == 1
    store.conn.close()


def test_cli_census_parser(tmp_path: Path, capsys) -> None:
    root = tmp_path / "scan"
    _git_init(root / "watch-clank", remote="https://github.com/anil-ganti-nbc/watch-clank.git")
    out = tmp_path / "census.json"
    db = tmp_path / "c.db"
    assert (
        main(
            [
                "--db",
                str(db),
                "census",
                "--roots",
                str(root),
                "--out",
                str(out),
                "--no-github",
                "--import",
            ]
        )
        == 0
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["candidates"]
    assert main(["--db", str(db), "list"]) == 0
    assert "watch-clank" in capsys.readouterr().out
