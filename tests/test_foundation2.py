"""Foundation 2: verified fleet adoption, coverage, sessions, Terminal."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clankops.census import load_census
from clankops.cli import main
from clankops.clock import FrozenClock
from clankops.db import connect_readonly
from clankops.enums import EventSource
from clankops.events import list_events
from clankops.readmodel import (
    coverage_report,
    dossier,
    fleet_home,
    ledger_fingerprint,
    open_sessions,
    stale_sessions,
)
from clankops.store import open_readonly_store, open_store
from clankops.terminal import dispatch, serve
from clankops.timefmt import parse_duration

CENSUS_FILE = Path(__file__).resolve().parents[1] / "data" / "bootstrap" / "clank_census.json"
T0 = datetime(2026, 9, 10, 4, 0, 0, tzinfo=timezone.utc)


def _census() -> dict:
    return {
        "candidates": [
            {
                "slug": "oem-radar",
                "name": "OEM Radar",
                "classification": "VERIFIED",
                "local_path": str(Path("C:/tmp/oem-radar")),
                "canonical_remote": "https://github.com/anil-ganti-nbc/oem-radar.git",
            },
            {
                "slug": "feature-phone-clank",
                "name": "Feature Phone Clank",
                "classification": "VERIFIED",
                "local_path": str(Path("C:/tmp/feature-phone-clank")),
                "canonical_remote": "https://github.com/anil-ganti-nbc/feature-phone-clank.git",
                "apparent_purpose": "Feature Phone Clank",
            },
            {
                "slug": "clank-ledger",
                "name": "clank-ledger",
                "classification": "VERIFIED",
                "local_path": "(github-only)",
                "canonical_remote": "https://github.com/anil-ganti-nbc/clank-ledger",
                "apparent_purpose": "editorial HIT/MISS evidence plane",
            },
            {
                "slug": "feature-phone-clank-docs-copy",
                "name": "Feature Phone Clank copy",
                "classification": "SUPPORT_COMPONENT",
                "local_path": str(Path("C:/tmp/docs/feature-phone-clank")),
                "canonical_remote": "https://github.com/anil-ganti-nbc/feature-phone-clank.git",
            },
            {
                "slug": "mystery",
                "classification": "PROBABLE",
                "local_path": str(Path("C:/tmp/mystery")),
            },
            {
                "slug": "clankops",
                "name": "ClankOps",
                "classification": "PROBABLE",
                "local_path": str(Path("C:/tmp/clankops")),
            },
            {"slug": "ghost", "classification": "UNKNOWN"},
            {"slug": "support-tool", "classification": "SUPPORT_COMPONENT"},
            {"slug": "docs-site", "classification": "NOT_A_CLANK"},
            {"slug": "old-clank", "classification": "NEEDS_RECONSTRUCTION"},
        ],
        "duplicates": [
            {
                "identity": "anil-ganti-nbc/feature-phone-clank",
                "paths": [
                    str(Path("C:/tmp/feature-phone-clank")),
                    str(Path("C:/tmp/docs/feature-phone-clank")),
                ],
            }
        ],
    }


@contextmanager
def running_terminal(db_path: Path, **kwargs):
    httpd = serve(db_path=db_path, host="127.0.0.1", port=0, **kwargs)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _http(url: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method)
    if method == "POST":
        req.data = b"{}"
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_adopt_verified_skips_probable_and_attaches_duplicate_paths(tmp_path: Path) -> None:
    db = tmp_path / "f2.db"
    store = open_store(db, actor="cursor")
    store.register_clank(
        "oem-radar",
        local_path=r"C:\tmp\oem-radar",
        remotes=["https://github.com/anil-ganti-nbc/oem-radar.git"],
    )
    stats = store.adopt_verified_census(_census(), actor="cursor", source=EventSource.RECONSTRUCTED)
    slugs = {c["slug"] for c in store.list_clanks()}
    assert "feature-phone-clank" in slugs
    assert "clank-ledger" in slugs
    assert "mystery" not in slugs
    assert "feature-phone-clank-docs-copy" not in slugs
    phone = store.clank_detail("feature-phone-clank")
    paths = {r["ref_value"] for r in phone["refs"] if r["ref_kind"] == "local_path"}
    assert any("docs" in p.replace("\\", "/") for p in paths)
    ledger = store.clank_detail("clank-ledger")
    assert not any(
        r["ref_kind"] == "local_path" and "(github-only)" in r["ref_value"]
        for r in ledger["refs"]
    )
    assert stats["registered"] >= 2
    assert stats["extra_duplicate_refs"] >= 1
    store.conn.close()


def test_terminal_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "term.db"
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar", display_name="OEM Radar")
    store.start_mission("oem-radar", "handheld")
    before = len(list_events(store.conn))
    store.conn.close()

    reader = open_readonly_store(db)
    status, ctype, body = dispatch(reader, "GET", "/")
    assert status == 200
    assert b"oem-radar" in body
    status, _, body = dispatch(reader, "GET", "/api/fleet")
    assert status == 200
    assert b"oem-radar" in body
    status, _, _ = dispatch(reader, "POST", "/api/fleet")
    assert status == 405
    status, _, body = dispatch(reader, "GET", "/clank/oem-radar")
    assert status == 200
    assert b"handheld" in body
    after = len(list_events(reader.conn))
    assert after == before
    reader.conn.close()

    conn = connect_readonly(db)
    try:
        conn.execute("INSERT INTO clanks(clank_id) VALUES ('nope')")
        conn.commit()
        raised = False
    except Exception:
        raised = True
    conn.close()
    assert raised


def test_cli_fleet_adopt_verified(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "cli.db")
    census_path = tmp_path / "census.json"
    census_path.write_text(json.dumps(_census()), encoding="utf-8")
    assert main(["--db", db, "--actor", "cursor", "register", "oem-radar"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--json",
                "fleet-adopt-verified",
                "--file",
                str(census_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["registered"] >= 1
    assert main(["--db", db, "--json", "list"]) == 0
    listed = {row["slug"] for row in json.loads(capsys.readouterr().out)}
    assert "feature-phone-clank" in listed
    assert "mystery" not in listed


def test_coverage_is_deterministic_and_ignores_clankops(tmp_path: Path, capsys) -> None:
    db = tmp_path / "cov.db"
    store = open_store(db, actor="cursor")
    store.register_clank("clankops", display_name="ClankOps")
    store.adopt_verified_census(_census(), actor="cursor", source=EventSource.RECONSTRUCTED)
    first = coverage_report(store, _census())
    second = coverage_report(store, _census())
    assert first == second
    assert first["census_candidates"] == 10
    assert first["verified_candidates"] == 3
    assert first["verified_registered"] == 3
    assert first["verified_unresolved_count"] == 0
    assert first["probable"] == 2
    assert first["unknown"] == 1
    assert first["support_component"] == 2
    assert first["not_a_clank"] == 1
    assert first["needs_reconstruction"] == 1
    assert first["registered_clanks"] == 4  # 3 VERIFIED + ClankOps
    assert first["registered_includes_clankops"] is True
    assert first["verified_candidates"] != first["registered_clanks"]
    assert first["duplicate_identity_groups"] == 1
    before = ledger_fingerprint(store)
    store.conn.close()

    census_path = tmp_path / "census.json"
    census_path.write_text(json.dumps(_census()), encoding="utf-8")
    assert main(["--db", str(db), "--json", "coverage", "--file", str(census_path)]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert cli["verified_candidates"] == 3
    assert cli["registered_clanks"] == 4
    reader = open_readonly_store(db)
    assert ledger_fingerprint(reader) == before
    reader.conn.close()


def test_real_census_verified_denominator_excludes_clankops(tmp_path: Path) -> None:
    census = load_census(CENSUS_FILE)
    verified = [c for c in census["candidates"] if c.get("classification") == "VERIFIED"]
    assert len(verified) == 17
    clankops = next(c for c in census["candidates"] if c.get("slug") == "clankops")
    assert clankops["classification"] == "PROBABLE"

    db = tmp_path / "real-cov.db"
    store = open_store(db, actor="cursor")
    store.register_clank("clankops", display_name="ClankOps")
    store.adopt_verified_census(census, actor="cursor", source=EventSource.RECONSTRUCTED)
    report = coverage_report(store, census)
    assert report["verified_candidates"] == 17
    assert report["verified_registered"] == 17
    assert report["verified_unresolved_count"] == 0
    assert report["registered_includes_clankops"] is True
    assert report["registered_clanks"] == 18
    assert "clankops" not in report["verified_registered_slugs"]
    store.conn.close()


def test_open_and_stale_sessions_frozen_clock_no_mutation(tmp_path: Path, capsys) -> None:
    db = tmp_path / "sess.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", display_name="OEM Radar")
    mission = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(
        mission["display_id"],
        branch="feature/f2",
        head="abcdef1234567890",
        working_tree="dirty",
        next_action="land Terminal alpha",
    )
    before = ledger_fingerprint(store)
    open_at_start = open_sessions(store, now=T0, stale_after=timedelta(hours=24))
    assert len(open_at_start) == 1
    assert open_at_start[0]["session_id"] == mission["session_id"]
    assert open_at_start[0]["clank_slug"] == "oem-radar"
    assert open_at_start[0]["mission_display"] == mission["display_id"]
    assert open_at_start[0]["mission_state"] == "ACTIVE"
    assert open_at_start[0]["stale"] is False
    assert open_at_start[0]["next_action"] == "land Terminal alpha"
    assert stale_sessions(store, older_than=timedelta(hours=24), now=T0 + timedelta(hours=23)) == []
    aged = stale_sessions(store, older_than=timedelta(hours=24), now=T0 + timedelta(hours=24))
    assert len(aged) == 1
    assert aged[0]["stale"] is True
    assert ledger_fingerprint(store) == before
    store.conn.close()

    assert main(["--db", str(db), "--json", "sessions", "open"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["session_id"] == mission["session_id"]
    assert main(["--db", str(db), "--json", "sessions", "stale", "--older-than", "24h"]) == 0
    capsys.readouterr()
    reader = open_readonly_store(db)
    assert ledger_fingerprint(reader) == before
    reader.conn.close()


def test_impossible_open_session_on_non_active_mission_is_anomaly(tmp_path: Path) -> None:
    db = tmp_path / "anom.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar")
    mission = store.start_mission("oem-radar", "handheld")
    sid = mission["session_id"]
    store.pause_mission(mission["display_id"])
    paused = store.resolve_mission(mission["display_id"])
    assert paused["state"] == "PAUSED"
    assert store.resolve_session(sid)["ended_utc"] is not None
    store.conn.execute("UPDATE sessions SET ended_utc = NULL WHERE session_id = ?", (sid,))
    store.conn.commit()
    before = ledger_fingerprint(store)
    rows = open_sessions(store, now=T0)
    assert len(rows) == 1
    assert rows[0]["anomaly_open_on_non_active_mission"] is True
    assert rows[0]["anomaly"] == "open Session attached to a non-ACTIVE Mission"
    assert ledger_fingerprint(store) == before
    store.conn.close()


def test_fleet_summary_and_sort_and_unknown_next_action(tmp_path: Path) -> None:
    db = tmp_path / "fleet.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("active-clank", display_name="Active")
    store.register_clank("blocked-clank", display_name="Blocked")
    store.register_clank("paused-clank", display_name="Paused")
    store.register_clank("done-clank", display_name="Done")
    a = store.start_mission("active-clank", "keep going")
    store.record_checkpoint(a["display_id"], branch="main", head="aaabbbbcccc", working_tree="clean")
    b = store.start_mission("blocked-clank", "waiting")
    store.block_mission(b["display_id"])
    p = store.start_mission("paused-clank", "later")
    store.pause_mission(p["display_id"])
    d = store.start_mission("done-clank", "finished")
    store.complete_mission(d["display_id"])
    home = fleet_home(store, census=_census(), now=T0, stale_after=timedelta(hours=24))
    summary = home["summary"]
    assert summary["registered_clanks"] == 4
    assert summary["active_missions"] == 1
    assert summary["blocked_missions"] == 1
    assert summary["paused_missions"] == 1
    assert summary["open_sessions"] == 1
    slugs = [row["slug"] for row in home["rows"]]
    assert slugs[:3] == ["active-clank", "blocked-clank", "paused-clank"]
    active = home["rows"][0]
    assert active["open_session_id"]
    assert active["actor"] == "cursor"
    assert active["branch"] == "main"
    assert active["head_short"] == "aaabbbb"
    assert active["working_tree"] == "clean"
    assert active["next_action"] is None
    html_page, _, body = dispatch(store, "GET", "/", census=_census())
    assert html_page == 200
    text = body.decode("utf-8")
    assert "[ACTIVE]" in text
    assert "unknown" in text
    assert "land Terminal alpha" not in text
    store.conn.close()


def test_dossier_sections_timeline_provenance_and_history(tmp_path: Path) -> None:
    db = tmp_path / "dos.db"
    store = open_store(db, actor="cursor", source=EventSource.RECONSTRUCTED, clock=FrozenClock(T0))
    store.register_clank("oem-radar", display_name="OEM Radar")
    first = store.start_mission("oem-radar", "first pass")
    store.complete_mission(first["display_id"])
    second = store.start_mission("oem-radar", "handheld", source=EventSource.USER)
    store.add_task(second["display_id"], "write adapters")
    store.add_feature("oem-radar", "handheld coverage")
    store.add_decision(second["display_id"], "Ship adapters before dashboard", why="ledger first")
    store.add_blocker(second["display_id"], "need live cookies")
    store.record_checkpoint(
        second["display_id"],
        completed="registered clank",
        current_work="adapters",
        tests="not run",
        branch="expansion",
        head="adb04cc0deadbeef",
        working_tree="dirty",
        git_evidence={"source": EventSource.LOCAL_GIT, "branch": "expansion", "head": "adb04cc0deadbeef"},
        source=EventSource.AGENT_REPORT,
    )
    store.attach_artifact(
        mission=second["display_id"],
        kind="PULL_REQUEST",
        ref="https://github.com/anil-ganti-nbc/oem-radar/pull/1",
        source=EventSource.GITHUB,
    )
    payload = dossier(store, "oem-radar", now=T0)
    now = payload["now"]
    assert now["mission_display"] == second["display_id"]
    assert now["mission_state"] == "ACTIVE"
    assert now["mission_objective"] == "handheld"
    assert now["open_sessions"]
    assert now["branch"] == "expansion"
    assert now["head"] == "adb04cc0deadbeef"
    assert now["working_tree"] == "dirty"
    assert now["tests"] == "not run"
    assert now["blockers"]
    assert now["outstanding_tasks"]
    assert now["next_action"] is None
    seqs = [row["ledger_seq"] for row in payload["timeline"]]
    assert seqs == sorted(seqs)
    assert seqs == list(range(min(seqs), max(seqs) + 1)) or seqs == sorted(seqs)
    sources = {label for row in payload["timeline"] for label in row["provenance"]}
    assert "RECONSTRUCTED" in sources
    assert "USER" in sources
    assert "AGENT_REPORT" in sources
    assert "LOCAL_GIT" in sources
    assert "GITHUB" in sources
    assert {m["display_id"] for m in payload["missions"]} == {first["display_id"], second["display_id"]}
    assert any(f["name"] == "handheld coverage" for f in payload["features"])
    assert any(t["title"] == "write adapters" for t in payload["tasks"])
    assert any("Ship adapters" in d["statement"] for d in payload["decisions"])

    status, _, body = dispatch(store, "GET", "/clank/oem-radar")
    assert status == 200
    html = body.decode("utf-8")
    assert ">NOW<" in html or "<h2>NOW</h2>" in html
    assert "<h2>TIMELINE</h2>" in html
    assert "<h2>MISSIONS</h2>" in html
    assert "<h2>FEATURES</h2>" in html
    assert "<h2>TASKS</h2>" in html
    assert "<h2>DECISIONS</h2>" in html
    for token in ("USER", "AGENT_REPORT", "LOCAL_GIT", "GITHUB", "RECONSTRUCTED"):
        assert token in html
    assert "unknown" in html
    assert "invent a next action" not in html.lower()
    store.conn.close()


def test_http_through_server_socket_is_read_only_and_thread_safe(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "net.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", display_name="OEM Radar")
    mission = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(
        mission["display_id"],
        branch="main",
        head="abc1234def",
        working_tree="clean",
        source=EventSource.USER,
    )
    before = ledger_fingerprint(store)
    store.conn.close()

    opens: list[int] = []
    real_open = open_readonly_store

    def counting_open(path, **kwargs):
        opens.append(threading.get_ident())
        return real_open(path, **kwargs)

    monkeypatch.setattr("clankops.terminal.open_readonly_store", counting_open)

    with running_terminal(db, census=_census(), clock=FrozenClock(T0)) as base:
        status, body = _http(f"{base}/api/fleet")
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        slugs = {row["slug"] for row in payload["rows"]}
        assert "oem-radar" in slugs
        assert payload["summary"]["registered_clanks"] >= 1
        status, body = _http(f"{base}/clank/oem-radar")
        assert status == 200
        assert b"handheld" in body
        assert b"<h2>NOW</h2>" in body
        status, _ = _http(f"{base}/api/fleet", method="POST")
        assert status == 405
        status, _ = _http(f"{base}/clank/oem-radar", method="POST")
        assert status == 405
        urls = [f"{base}/api/fleet", f"{base}/clank/oem-radar"] * 6
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_http, urls))
        assert all(status == 200 for status, _ in results)

    assert len(opens) >= 14
    assert len(set(opens)) >= 2
    reader = open_readonly_store(db)
    assert ledger_fingerprint(reader) == before
    reader.conn.close()


def test_parse_duration() -> None:
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("7d") == timedelta(days=7)
