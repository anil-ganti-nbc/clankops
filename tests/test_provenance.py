"""Checkpoint provenance: semantic claims vs LOCAL_GIT evidence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from clankops.cli import main, resolve_invocation_source
from clankops.enums import EventSource
from clankops.errors import ValidationError
from clankops.events import list_events
from clankops.store import open_store


def _git_init(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    def run(args: list[str]) -> None:
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
    run(["init", "-b", "foundation-1-live-fleet-adoption"])
    run(["config", "user.email", "ops@example.test"])
    run(["config", "user.name", "Ops"])
    (path / "README.md").write_text("toy\n", encoding="utf-8")
    run(["add", "README.md"])
    run(["commit", "-m", "init"])
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return proc.stdout.strip()


def _checkpoint_event(store, mission_id: str):
    events = [
        e
        for e in list_events(store.conn, mission_id=mission_id)
        if e.event_type == "CHECKPOINT_RECORDED"
    ]
    assert events
    return events[-1]


def test_resolve_invocation_source_defaults() -> None:
    assert resolve_invocation_source("user", None) == EventSource.USER
    assert resolve_invocation_source("operator", None) == EventSource.USER
    assert resolve_invocation_source("cursor", None) == EventSource.AGENT_REPORT
    assert resolve_invocation_source("cursor", EventSource.USER) == EventSource.USER
    assert resolve_invocation_source("cursor", EventSource.RECONSTRUCTED) == EventSource.RECONSTRUCTED


def test_cursor_semantic_checkpoint_is_agent_report(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "p.db")
    repo = tmp_path / "toy"
    head = _git_init(repo)
    assert main(["--db", db, "--actor", "cursor", "register", "toy-clank", "--path", str(repo)]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "work", "start", "toy-clank", "provenance"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--json",
                "checkpoint",
                "COPS-000001",
                "--completed",
                "semantic work",
                "--current",
                "still coding",
                "--next",
                "tests",
                "--notes",
                "agent-written claims",
                "--capture-git",
            ]
        )
        == 0
    )
    row = json.loads(capsys.readouterr().out)
    assert row["source"] == EventSource.AGENT_REPORT
    assert row["completed"] == "semantic work"
    evidence = row["git_evidence"]
    assert evidence["source"] == EventSource.LOCAL_GIT
    assert evidence["head"] == head
    assert evidence["branch"] == "foundation-1-live-fleet-adoption"
    assert row["source"] != EventSource.LOCAL_GIT
    assert row["source"] != EventSource.GITHUB
    assert evidence["source"] != EventSource.GITHUB

    store = open_store(db, actor="cursor")
    event = _checkpoint_event(store, row["mission_id"])
    assert event.source == EventSource.AGENT_REPORT
    assert event.payload["completed"] == "semantic work"
    assert event.payload["git_evidence"]["source"] == EventSource.LOCAL_GIT
    assert event.payload["git_evidence"]["head"] == head
    store.conn.close()


def test_explicit_user_and_reconstructed_survive_git_capture(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "p.db")
    repo = tmp_path / "toy"
    _git_init(repo)
    assert main(["--db", db, "--actor", "cursor", "register", "toy-clank", "--path", str(repo)]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "work", "start", "toy-clank", "sources"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--source",
                "USER",
                "--json",
                "checkpoint",
                "COPS-000001",
                "--completed",
                "human said this",
                "--capture-git",
            ]
        )
        == 0
    )
    user_row = json.loads(capsys.readouterr().out)
    assert user_row["source"] == EventSource.USER
    assert user_row["git_evidence"]["source"] == EventSource.LOCAL_GIT

    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--source",
                "RECONSTRUCTED",
                "--json",
                "checkpoint",
                "COPS-000001",
                "--completed",
                "inferred later",
                "--capture-git",
            ]
        )
        == 0
    )
    recon = json.loads(capsys.readouterr().out)
    assert recon["source"] == EventSource.RECONSTRUCTED
    assert recon["git_evidence"]["source"] == EventSource.LOCAL_GIT
    assert recon["source"] != EventSource.GITHUB


def test_branch_without_capture_git_is_not_local_git(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "p.db")
    assert main(["--db", db, "--actor", "cursor", "register", "toy-clank"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "work", "start", "toy-clank", "no git"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--json",
                "checkpoint",
                "COPS-000001",
                "--branch",
                "someone-typed-this",
                "--current",
                "claims only",
            ]
        )
        == 0
    )
    row = json.loads(capsys.readouterr().out)
    assert row["source"] == EventSource.AGENT_REPORT
    assert row["git_evidence"] is None
    assert row["branch"] == "someone-typed-this"


def test_git_evidence_cannot_claim_github(tmp_path: Path) -> None:
    db = str(tmp_path / "p.db")
    store = open_store(db, actor="cursor")
    store.register_clank("toy-clank")
    mission = store.start_mission("toy-clank", "no github from git")
    with pytest.raises(ValidationError, match="GitHub facts cannot be manufactured"):
        store.record_checkpoint(
            mission["display_id"],
            completed="nope",
            git_evidence={"source": EventSource.GITHUB, "head": "abc"},
            source=EventSource.AGENT_REPORT,
        )
    store.conn.close()


def test_resume_mission_keeps_requested_source(tmp_path: Path) -> None:
    db = str(tmp_path / "p.db")
    store = open_store(db, actor="cursor")
    store.register_clank("toy-clank")
    mission = store.start_mission("toy-clank", "resume source", actor="cursor")
    store.pause_mission(mission["display_id"], actor="cursor")
    store.resume_mission(
        mission["display_id"],
        actor="cursor",
        source=EventSource.AGENT_REPORT,
    )
    events = list_events(store.conn, mission_id=mission["mission_id"])
    changed = [e for e in events if e.event_type == "MISSION_STATE_CHANGED"][-1]
    started = [e for e in events if e.event_type == "SESSION_STARTED"][-1]
    assert changed.source == EventSource.AGENT_REPORT
    assert started.source == EventSource.AGENT_REPORT
    assert changed.payload["to_state"] == "ACTIVE"
    store.conn.close()


def test_handoff_does_not_relabel_semantics_local_git(tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "p.db")
    repo = tmp_path / "toy"
    head = _git_init(repo)
    assert main(["--db", db, "--actor", "cursor", "register", "toy-clank", "--path", str(repo)]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "work", "start", "toy-clank", "handoff src"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--actor",
                "cursor",
                "--json",
                "handoff",
                "COPS-000001",
                "--completed",
                "paused by agent",
                "--next",
                "merge after review",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    cp = payload["checkpoint"]
    assert cp["source"] == EventSource.AGENT_REPORT
    assert cp["completed"] == "paused by agent"
    assert cp["git_evidence"]["source"] == EventSource.LOCAL_GIT
    assert cp["git_evidence"]["head"] == head
    assert cp["source"] != EventSource.GITHUB
    assert cp["git_evidence"]["source"] != EventSource.GITHUB
