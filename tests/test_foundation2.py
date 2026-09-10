"""Foundation 2: verified fleet adoption and read-only Terminal."""

from __future__ import annotations

from pathlib import Path

from clankops.cli import main
from clankops.db import connect_readonly
from clankops.enums import EventSource
from clankops.events import list_events
from clankops.store import open_readonly_store, open_store
from clankops.terminal import dispatch


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
        ]
    }


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

    # query_only / mode=ro must reject writes
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
    import json

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
