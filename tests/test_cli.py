import json
import sqlite3
from pathlib import Path

from clankops.cli import main


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "cli.db")


def test_cli_happy_paths(tmp_path: Path, capsys) -> None:
    db = _db(tmp_path)
    assert main(["--db", db, "init"]) == 0
    assert main(["--db", db, "register", "oem-radar", "--name", "OEM Radar", "--alias", "radar"]) == 0
    assert main(["--db", db, "mission", "start", "oem-radar", "Add handheld OEMs"]) == 0
    captured = capsys.readouterr()
    assert "COPS-000001" in captured.out
    assert main(["--db", db, "task", "add", "COPS-000001", "write adapters"]) == 0
    task_out = capsys.readouterr().out
    task_id = task_out.split()[1]
    assert main(["--db", db, "feature", "add", "oem-radar", "handheld coverage"]) == 0
    assert main(
        [
            "--db",
            db,
            "decision",
            "add",
            "COPS-000001",
            "Ship adapters before dashboard",
            "--why",
            "ledger first",
        ]
    ) == 0
    assert main(["--db", db, "blocker", "add", "COPS-000001", "need live cookies"]) == 0
    assert (
        main(
            [
                "--db",
                db,
                "checkpoint",
                "COPS-000001",
                "--completed",
                "registered clank",
                "--current",
                "adapters",
                "--next",
                "run collector against Sixunited",
                "--tests",
                "not run",
                "--branch",
                "expansion-handheld-sixunited-m1",
                "--head",
                "adb04cc0",
            ]
        )
        == 0
    )
    assert main(["--db", db, "task", "done", task_id]) == 0
    assert main(["--db", db, "list"]) == 0
    assert "oem-radar" in capsys.readouterr().out
    assert main(["--db", db, "show", "radar"]) == 0
    assert main(["--db", db, "mission", "list", "oem-radar"]) == 0
    assert main(["--db", db, "brief", "oem-radar"]) == 0
    brief = capsys.readouterr().out
    assert "COPS-000001" in brief
    assert "run collector against Sixunited" in brief
    assert "refusing to invent" not in brief
    assert main(["--db", db, "history", "oem-radar"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "mission", "pause", "COPS-000001"]) == 0
    assert main(["--db", db, "mission", "resume", "COPS-000001"]) == 0
    assert main(["--db", db, "mission", "complete", "COPS-000001"]) == 0
    # second mission to abandon
    assert main(["--db", db, "mission", "start", "oem-radar", "dead end"]) == 0
    assert main(["--db", db, "mission", "abandon", "COPS-000002"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--json", "brief", "oem-radar"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["identity"]["slug"] == "oem-radar"


def test_cli_invalid_inputs(tmp_path: Path, capsys) -> None:
    db = _db(tmp_path)
    assert main(["--db", db, "show", "nope"]) == 2
    err = capsys.readouterr().err
    assert "not found" in err
    main(["--db", db, "register", "x"])
    capsys.readouterr()
    assert main(["--db", db, "mission", "start", "x", "go"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "mission", "complete", "COPS-000001"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "mission", "resume", "COPS-000001"]) == 2
    assert "cannot resume" in capsys.readouterr().err
    assert main(["--db", db, "register", "x"]) == 2


def test_cli_rebuild(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert main(["--db", db, "register", "oem-radar"]) == 0
    assert main(["--db", db, "rebuild"]) == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM clanks").fetchone()[0] == 1
    conn.close()
