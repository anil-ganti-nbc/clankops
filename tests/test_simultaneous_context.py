"""Per-Clank context: OEM Radar and Watch Clank can be open at once."""

from __future__ import annotations

import json
from pathlib import Path

from clankops.cli import main
from clankops.context import (
    active_env_path,
    clank_context_path,
    last_active_path,
    read_clank_context,
    session_snapshot_path,
)


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "ctx.db")


def _start(capsys, db: str, slug: str, path: Path, objective: str) -> dict:
    assert main(["--db", db, "--actor", "cursor", "register", slug, "--path", str(path)]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "--json", "work", "start", slug, objective]) == 0
    return json.loads(capsys.readouterr().out)


def test_concurrent_clank_contexts_are_independent(tmp_path: Path, monkeypatch, capsys) -> None:
    oem = tmp_path / "oem-radar"
    watch = tmp_path / "watch-clank"
    oem.mkdir()
    watch.mkdir()
    db = _db(tmp_path)

    oem_ctx = _start(capsys, db, "oem-radar", oem, "handheld expansion")
    watch_ctx = _start(capsys, db, "watch-clank", watch, "casio health")

    oem_file = Path(oem_ctx["context_file"])
    watch_file = Path(watch_ctx["context_file"])
    assert oem_file != watch_file
    assert oem_file.exists() and watch_file.exists()
    assert json.loads(oem_file.read_text(encoding="utf-8"))["session_id"] == oem_ctx["session_id"]
    assert json.loads(watch_file.read_text(encoding="utf-8"))["session_id"] == watch_ctx["session_id"]

    # Writing Watch did not destroy OEM.
    still_oem = read_clank_context(oem_ctx["clank_id"])
    assert still_oem["session_id"] == oem_ctx["session_id"]
    assert still_oem["clank_slug"] == "oem-radar"

    last = json.loads(last_active_path().read_text(encoding="utf-8"))
    assert last["clank_id"] == watch_ctx["clank_id"]

    monkeypatch.chdir(watch)
    monkeypatch.setenv("CLANKOPS_SESSION_ID", oem_ctx["session_id"])
    monkeypatch.setenv("CLANKOPS_CLANK_ID", oem_ctx["clank_id"])
    monkeypatch.setenv("CLANKOPS_MISSION_ID", oem_ctx["mission_id"])
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 2
    err = capsys.readouterr().err
    assert "CLANKOPS INTEGRATION FAILED" in err
    assert "watch-clank" in err

    monkeypatch.chdir(oem)
    monkeypatch.setenv("CLANKOPS_SESSION_ID", watch_ctx["session_id"])
    monkeypatch.setenv("CLANKOPS_CLANK_ID", watch_ctx["clank_id"])
    monkeypatch.setenv("CLANKOPS_MISSION_ID", watch_ctx["mission_id"])
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 2
    err = capsys.readouterr().err
    assert "CLANKOPS INTEGRATION FAILED" in err
    assert "oem-radar" in err

    monkeypatch.delenv("CLANKOPS_SESSION_ID", raising=False)
    monkeypatch.delenv("CLANKOPS_CLANK_ID", raising=False)
    monkeypatch.delenv("CLANKOPS_MISSION_ID", raising=False)

    monkeypatch.chdir(oem)
    assert main(["--db", db, "--actor", "cursor", "--json", "work", "env"]) == 0
    oem_ok = json.loads(capsys.readouterr().out)
    assert oem_ok["session_id"] == oem_ctx["session_id"]

    monkeypatch.chdir(watch)
    assert main(["--db", db, "--actor", "cursor", "--json", "work", "env"]) == 0
    watch_ok = json.loads(capsys.readouterr().out)
    assert watch_ok["session_id"] == watch_ctx["session_id"]

    # last-active pointing at Watch is not enough inside OEM.
    monkeypatch.chdir(oem)
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 0
    capsys.readouterr()
    assert json.loads(clank_context_path(watch_ctx["clank_id"]).read_text(encoding="utf-8"))[
        "session_id"
    ] == watch_ctx["session_id"]


def test_handoff_preserves_snapshot_and_stops_advertising(tmp_path: Path, monkeypatch, capsys) -> None:
    oem = tmp_path / "oem-radar"
    watch = tmp_path / "watch-clank"
    oem.mkdir()
    watch.mkdir()
    db = _db(tmp_path)
    oem_ctx = _start(capsys, db, "oem-radar", oem, "radar")
    watch_ctx = _start(capsys, db, "watch-clank", watch, "watch")

    monkeypatch.setenv("CLANKOPS_SESSION_ID", oem_ctx["session_id"])
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "handoff",
                "COPS-000001",
                "--completed",
                "oem pause",
                "--next",
                "resume oem",
            ]
        )
        == 0
    )
    capsys.readouterr()

    oem_after = read_clank_context(oem_ctx["clank_id"])
    assert oem_after["session_ended"] is True
    assert oem_after["active"] is False
    assert "CLANKOPS_SESSION_ID" not in (oem_after.get("env") or {})

    snap = session_snapshot_path(oem_ctx["session_id"])
    assert snap.exists()
    assert json.loads(snap.read_text(encoding="utf-8"))["session_id"] == oem_ctx["session_id"]

    watch_after = read_clank_context(watch_ctx["clank_id"])
    assert watch_after["session_id"] == watch_ctx["session_id"]
    assert watch_after["active"] is True

    env_file = active_env_path()
    if env_file.exists():
        assert oem_ctx["session_id"] not in env_file.read_text(encoding="utf-8")

    monkeypatch.delenv("CLANKOPS_SESSION_ID", raising=False)
    monkeypatch.delenv("CLANKOPS_CLANK_ID", raising=False)
    monkeypatch.chdir(oem)
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 2
    assert "CLANKOPS INTEGRATION FAILED" in capsys.readouterr().err

    monkeypatch.chdir(watch)
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 0
    capsys.readouterr()
