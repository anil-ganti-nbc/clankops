"""Foundation 1 adoption: work/resume, handoff, Session env, no silent failure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clankops.cli import main
from clankops.context import read_context
from clankops.errors import ValidationError
from clankops.store import open_store


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "adopt.db")


def test_work_resume_exports_context_and_handoff_pauses(tmp_path: Path, capsys) -> None:
    db = _db(tmp_path)
    assert main(["--db", db, "--actor", "cursor", "register", "oem-radar", "--path", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "work", "start", "oem-radar", "handheld expansion"]) == 0
    started = capsys.readouterr().out
    assert "COPS-000001" in started
    assert "CLANKOPS_SESSION_ID" in started
    ctx = read_context()
    assert ctx is not None
    first_session = ctx["session_id"]
    assert ctx["env"]["CLANKOPS_CLANK_SLUG"] == "oem-radar"

    assert main(["--db", db, "--actor", "cursor", "--session", first_session, "task", "add", "COPS-000001", "write adapters"]) == 0
    capsys.readouterr()
    assert main(
        [
            "--db",
            db,
            "--actor",
            "cursor",
            "--session",
            first_session,
            "handoff",
            "COPS-000001",
            "--state",
            "PAUSED",
            "--completed",
            "registered and opened",
            "--current",
            "stopping",
            "--next",
            "resume adapters",
        ]
    ) == 0
    capsys.readouterr()

    assert main(["--db", db, "--actor", "cursor", "work", "resume", "oem-radar"]) == 0
    resumed = capsys.readouterr().out
    assert "resumed COPS-000001" in resumed
    ctx2 = read_context()
    assert ctx2 is not None
    assert ctx2["session_id"] != first_session
    assert ctx2["mission_display"] == "COPS-000001"

    assert main(["--db", db, "--actor", "cursor", "--json", "brief", "oem-radar"]) == 0
    brief = json.loads(capsys.readouterr().out)
    assert brief["mission"]["display_id"] == "COPS-000001"
    assert brief["checkpoint"]["next_action"] == "resume adapters"
    assert brief["outstanding_tasks"][0]["title"] == "write adapters"


def test_work_start_refuses_to_invent_a_second_mission(tmp_path: Path, capsys) -> None:
    db = _db(tmp_path)
    assert main(["--db", db, "--actor", "cursor", "register", "oem-radar"]) == 0
    assert main(["--db", db, "--actor", "cursor", "work", "start", "oem-radar", "first"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "work", "start", "oem-radar", "second"]) == 2
    assert "unfinished Mission already exists" in capsys.readouterr().err


def test_work_env_rejects_stale_and_cross_clank_session(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CLANKOPS_HOME", str(home))
    db = _db(tmp_path)
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar")
    store.register_clank("watch-clank")
    radar = store.start_mission("oem-radar", "radar work")
    watch = store.start_mission("watch-clank", "casio work")
    radar_clank_id = radar["clank_id"]
    watch_session = watch["session_id"]
    store.conn.close()

    monkeypatch.setenv("CLANKOPS_SESSION_ID", watch_session)
    monkeypatch.setenv("CLANKOPS_CLANK_ID", radar_clank_id)
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 2
    err = capsys.readouterr().err
    assert "CLANKOPS INTEGRATION FAILED" in err
    assert "Emergency coding is not blocked" in err

    monkeypatch.delenv("CLANKOPS_CLANK_ID", raising=False)
    monkeypatch.setenv("CLANKOPS_SESSION_ID", "not-a-session")
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 2
    assert "CLANKOPS INTEGRATION FAILED" in capsys.readouterr().err


def test_handoff_acceptance_path_preserves_history(tmp_path: Path, monkeypatch, capsys) -> None:
    db = _db(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    assert main(["--db", db, "--actor", "cursor", "register", "toy-clank", "--path", str(repo)]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "--json", "work", "start", "toy-clank", "acceptance"]) == 0
    payload = json.loads(capsys.readouterr().out)
    sid1 = payload["session_id"]
    assert payload["env"]["CLANKOPS_MISSION_DISPLAY"] == "COPS-000001"

    monkeypatch.setenv("CLANKOPS_SESSION_ID", sid1)
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "decision",
                "add",
                "COPS-000001",
                "Keep ledger boring",
                "--why",
                "invariants first",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "feature", "add", "toy-clank", "handoff command"]) == 0
    capsys.readouterr()
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
                "acceptance slice",
                "--current",
                "paused",
                "--next",
                "resume later",
                "--tests",
                "python -m pytest: recorded",
            ]
        )
        == 0
    )
    capsys.readouterr()

    monkeypatch.delenv("CLANKOPS_SESSION_ID", raising=False)
    assert main(["--db", db, "--actor", "cursor", "work", "env"]) == 2
    assert "CLANKOPS INTEGRATION FAILED" in capsys.readouterr().err

    assert main(["--db", db, "--actor", "cursor", "--json", "work", "resume", "toy-clank"]) == 0
    payload2 = json.loads(capsys.readouterr().out)
    assert payload2["session_id"] != sid1
    assert main(["--db", db, "--actor", "cursor", "--json", "brief", "toy-clank"]) == 0
    brief = json.loads(capsys.readouterr().out)
    assert brief["checkpoint"]["completed"] == "acceptance slice"
    assert brief["next_action"] == "resume later"
    assert any(d["statement"] == "Keep ledger boring" for d in brief["decisions"])
    assert any(f["name"] == "handoff command" for f in brief["features"])


def test_dev_script_fails_visibly() -> None:
    text = Path("scripts/clankops-dev.ps1").read_text(encoding="utf-8")
    assert "CLANKOPS INTEGRATION FAILED" in text
    assert "Emergency coding is not blocked" in text
    assert "Run Collection" not in text
    assert '-ge "3.14"' not in text
    assert "[int]::TryParse" in text
    assert "Invoke-ClankOpsCursor" in text
    assert "Clear-ClankOpsActiveEnv" in text


def test_cross_clank_session_cannot_checkpoint(tmp_path: Path) -> None:
    db = _db(tmp_path)
    store = open_store(db, actor="cursor")
    store.register_clank("oem-radar")
    store.register_clank("watch-clank")
    radar = store.start_mission("oem-radar", "radar")
    watch = store.start_mission("watch-clank", "watch")
    with pytest.raises(ValidationError, match="another clank|another mission"):
        store.record_checkpoint(
            radar["display_id"],
            current_work="contaminated",
            session_id=watch["session_id"],
        )
    store.conn.close()
