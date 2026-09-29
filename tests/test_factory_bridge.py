"""Factory gates are ledger state; Hermes is a report, not an acceptance oracle."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from clankops.cli import main
from clankops.errors import DuplicateError, InvalidTransitionError, ValidationError
from clankops.events import list_events
from clankops.factory import advance, freeze, record_gate, record_review, select_candidate, status
from clankops.hermes_bridge import review
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.reconciliation import reconcile_mission

SHA = "a" * 40
NEXT = "b" * 40
CONTRACT = b"frozen agreement\n"


def manifest(mission: str) -> dict:
    return {
        "schema_version": 1, "mission": mission,
        "contract_sha256": hashlib.sha256(CONTRACT).hexdigest(),
        "start_phase": "BUILD",
        "phases": ["BUILD", "TEST", "REVIEW", "REWORK", "ACCEPTANCE", "CLOSED"],
        "terminal_results": ["PASS", "ROLLED_BACK", "HUMAN_REQUIRED", "FAILED"],
        "gates": [
            {"key": "contract_frozen", "scope": "contract", "description": "Frozen contract hash"},
            {"key": "tests_green", "scope": "candidate", "description": "Full tests pass"},
            {"key": "sol_review", "scope": "candidate", "description": "Independent review"},
        ],
        "transitions": [
            {"from": "BUILD", "to": "TEST", "requires": []},
            {"from": "TEST", "to": "REVIEW", "requires": ["tests_green"]},
            {"from": "REVIEW", "to": "REWORK", "requires": []},
            {"from": "REWORK", "to": "BUILD", "requires": []},
            {"from": "REVIEW", "to": "ACCEPTANCE", "requires": ["sol_review"]},
            {"from": "ACCEPTANCE", "to": "CLOSED", "requires": []},
        ],
    }


def seeded(store):
    store.register_clank("board-clank")
    mission = store.start_mission("board-clank", "CNX pilot")
    freeze(store, mission["display_id"], manifest(mission["display_id"]), CONTRACT)
    return mission["display_id"]


def test_frozen_gate_state_and_rework_replay(store) -> None:
    mission = seeded(store)
    with pytest.raises(DuplicateError):
        freeze(store, mission, manifest(mission), CONTRACT)
    bad = manifest(mission)
    with pytest.raises(ValidationError):
        freeze(store, mission, bad, b"tampered")
    with pytest.raises(ValidationError):
        record_gate(store, mission, "tests_green", "PASS", "test:1", "CI")
    select_candidate(store, mission, SHA)
    with pytest.raises(ValidationError):
        record_gate(store, mission, "sol_review", "PASS", "model says PASS", "AGENT_REPORT")
    with pytest.raises(InvalidTransitionError):
        advance(store, mission, "REVIEW", expect_phase="BUILD")
    advance(store, mission, "TEST", expect_phase="BUILD", expect_candidate=SHA)
    record_gate(store, mission, "tests_green", "PASS", "ci:run:1", "CI")
    advance(store, mission, "REVIEW", expect_phase="TEST", expect_candidate=SHA)
    advance(store, mission, "REWORK", expect_phase="REVIEW")
    select_candidate(store, mission, NEXT)
    assert "tests_green" not in status(store, mission)["gate_results"]
    advance(store, mission, "BUILD", expect_phase="REWORK")
    advance(store, mission, "TEST", expect_phase="BUILD")
    with pytest.raises(InvalidTransitionError):
        advance(store, mission, "REVIEW", expect_phase="TEST")
    record_gate(store, mission, "tests_green", "PASS", "ci:run:2", "CI")
    advance(store, mission, "REVIEW", expect_phase="TEST")
    record_gate(store, mission, "sol_review", "PASS", "review:2", "USER")
    with pytest.raises(InvalidTransitionError, match="Hermes review"):
        advance(store, mission, "ACCEPTANCE", expect_phase="REVIEW")
    record_review(store, mission, {"candidate_sha": NEXT, "completed_call": True,
                                   "reported_verdict": "PASS"})
    advance(store, mission, "ACCEPTANCE", expect_phase="REVIEW")
    with pytest.raises(InvalidTransitionError, match="contract_frozen"):
        advance(store, mission, "CLOSED", expect_phase="ACCEPTANCE")
    record_gate(store, mission, "contract_frozen", "PASS", "sha256:agreement", "USER")
    advance(store, mission, "CLOSED", expect_phase="ACCEPTANCE", expect_candidate=NEXT)
    assert status(store, mission)["terminal_result"] == "PASS"
    assert store.complete_mission(mission)["state"] == "COMPLETED"
    before = dump_projection_state(store.conn)
    rebuild_projections(store.conn)
    assert dump_projection_state(store.conn) == before
    assert any(e.event_type == "FACTORY_PHASE_ADVANCED" for e in list_events(store.conn))


def test_factory_completion_guard_has_no_partial_handoff(store) -> None:
    mission = seeded(store)
    before = len(list_events(store.conn))
    with pytest.raises(ValidationError, match="factory gates"):
        store.complete_mission(mission)
    with pytest.raises(ValidationError, match="factory gates"):
        store.handoff_mission(mission, "COMPLETED", completed="done")
    assert len(list_events(store.conn)) == before
    assert store.resolve_mission(mission)["state"] == "ACTIVE"


def test_factory_completion_guard_also_applies_to_reconciliation(store) -> None:
    mission = seeded(store)
    store.pause_mission(mission)
    before = len(list_events(store.conn))
    with pytest.raises(ValidationError, match="factory gates"):
        reconcile_mission(store, mission, to_state="COMPLETED", reason="premature",
                          evidence=["github:pr:6"], basis="github", actor="user",
                          source="USER")
    assert len(list_events(store.conn)) == before
    assert store.resolve_mission(mission)["state"] == "PAUSED"


def test_hermes_report_is_bound_to_clean_commit_and_never_passes_gate(store, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    mission = seeded(store)
    select_candidate(store, mission, SHA)
    advance(store, mission, "TEST", expect_phase="BUILD")
    record_gate(store, mission, "tests_green", "PASS", "ci:1", "CI")
    advance(store, mission, "REVIEW", expect_phase="TEST")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Review the exact commit for security and correctness.", encoding="utf-8")
    contract = tmp_path / "contract.md"
    contract.write_bytes(CONTRACT)
    monkeypatch.setattr("clankops.hermes_bridge.inspect_git",
                        lambda _path: {"head": SHA, "dirty": False})
    original = subprocess.run
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        Path(argv[argv.index("--usage-file") + 1]).write_text(
            json.dumps({"model": "gpt-5.6-sol", "provider": "openai-codex",
                        "completed": True, "failed": False}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "REQUEST_CHANGES: redirect risk", "")

    monkeypatch.setattr("clankops.hermes_bridge.subprocess.run", fake_run)
    response = review(store, mission, candidate_sha=SHA, review_id="review-1", checkout=checkout,
                      prompt_file=prompt, contract_file=contract, hermes="fake-hermes")
    assert response["review"]["reported_verdict"] == "REQUEST_CHANGES"
    assert response["review"]["completed_call"] is True
    assert calls[0][1:4] == ["--safe-mode", "-t", "bot_room"]
    assert "Contract sha256:" in calls[0][calls[0].index("-z") + 1]
    assert "sol_review" not in status(store, mission)["gate_results"]
    replay = review(store, mission, candidate_sha=SHA, review_id="review-1", checkout=checkout,
                    prompt_file=prompt, contract_file=contract, hermes="fake-hermes")
    assert replay["replayed"] is True and replay["response"] == "" and len(calls) == 1
    review(store, mission, candidate_sha=SHA, review_id="review-2", checkout=checkout,
           prompt_file=prompt, contract_file=contract, hermes="fake-hermes")
    old_replay = review(store, mission, candidate_sha=SHA, review_id="review-1", checkout=checkout,
                        prompt_file=prompt, contract_file=contract, hermes="fake-hermes")
    assert old_replay["replayed"] is True and len(calls) == 2
    with pytest.raises(ValidationError, match="changed"):
        review(store, mission, candidate_sha=SHA, review_id="review-3", checkout=checkout,
               prompt_file=prompt, contract_file=prompt, hermes="fake-hermes")
    with pytest.raises(InvalidTransitionError):
        advance(store, mission, "ACCEPTANCE", expect_phase="REVIEW")
    monkeypatch.setattr("clankops.hermes_bridge.subprocess.run", original)
    assert status(store, mission)["last_review"]["response_sha256"]


def test_cli_freeze_and_read_only_status(store, db_path: Path, tmp_path: Path, capsys) -> None:
    store.register_clank("board-clank")
    mission = store.start_mission("board-clank", "CNX pilot")["display_id"]
    contract = tmp_path / "contract.md"
    contract.write_bytes(CONTRACT)
    plan = tmp_path / "manifest.json"
    plan.write_text(json.dumps(manifest(mission)), encoding="utf-8")
    args = ["--db", str(db_path), "--actor", "tester", "--json", "factory"]
    assert main([*args, "freeze", mission, "--manifest", str(plan), "--contract", str(contract)]) == 0
    capsys.readouterr()
    assert main([*args, "status", mission]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "BUILD"
    assert payload["contract_sha256"] == hashlib.sha256(CONTRACT).hexdigest()


def test_hermes_model_mismatch_and_dirty_checkout_fail_closed(store, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    mission = seeded(store)
    select_candidate(store, mission, SHA)
    advance(store, mission, "TEST", expect_phase="BUILD")
    record_gate(store, mission, "tests_green", "PASS", "ci:1", "CI")
    advance(store, mission, "REVIEW", expect_phase="TEST")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Review this candidate.", encoding="utf-8")
    contract = tmp_path / "contract.md"
    contract.write_bytes(CONTRACT)
    observed = {"head": SHA, "dirty": True}
    monkeypatch.setattr("clankops.hermes_bridge.inspect_git", lambda _path: observed.copy())
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        Path(argv[argv.index("--usage-file") + 1]).write_text(
            json.dumps({"model": "grok-4.7", "provider": "openai-codex",
                        "completed": True}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "PASS: looks good", "")

    monkeypatch.setattr("clankops.hermes_bridge.subprocess.run", fake_run)
    with pytest.raises(ValidationError, match="clean"):
        review(store, mission, candidate_sha=SHA, review_id="review-1", checkout=checkout,
               prompt_file=prompt, contract_file=contract, hermes="fake-hermes")
    assert calls == []
    observed["dirty"] = False
    result = review(store, mission, candidate_sha=SHA, review_id="review-1", checkout=checkout,
                    prompt_file=prompt, contract_file=contract, hermes="fake-hermes")
    assert result["review"]["completed_call"] is False
    assert result["review"]["reported_verdict"] == "UNVERIFIED"
    assert status(store, mission)["gate_results"].get("sol_review") is None
    with pytest.raises(InvalidTransitionError, match="Hermes review"):
        advance(store, mission, "ACCEPTANCE", expect_phase="REVIEW")


def test_cnx_manifest_generator_preserves_all_frozen_gate_keys(store) -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "factory" / "make_cnx_manifest.py"
    spec = importlib.util.spec_from_file_location("make_cnx_manifest", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EXPECTED_CONTRACT = hashlib.sha256(CONTRACT).hexdigest()
    plan = module.build(CONTRACT, "REWORK")
    assert len(plan["gates"]) == 17
    assert plan["gates"][7]["key"] == "independent_sol_review"
    assert plan["phases"][:11] == ["INTAKE", "RECON", "SPEC", "BUILD", "TEST",
                                    "REVIEW", "ACCEPTANCE", "DEPLOY", "SOAK",
                                    "PROMOTE", "CLOSED"]
    assert "PASS" not in plan["phases"]
    assert "PASS" in plan["terminal_results"]
    assert any(edge["from"] == "PROMOTE" and edge["to"] == "CLOSED"
               for edge in plan["transitions"])
    store.register_clank("board-clank")
    mission = store.start_mission("board-clank", "CNX")
    plan["mission"] = mission["display_id"]
    assert freeze(store, mission["display_id"], plan, CONTRACT)["phase"] == "REWORK"


def test_cli_routes_hermes_review_with_exact_mission_and_contract(store, db_path: Path,
                                                                   tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch,
                                                                   capsys) -> None:
    mission = seeded(store)
    select_candidate(store, mission, SHA)
    advance(store, mission, "TEST", expect_phase="BUILD")
    record_gate(store, mission, "tests_green", "PASS", "ci:1", "CI")
    advance(store, mission, "REVIEW", expect_phase="TEST")
    prompt = tmp_path / "prompt.txt"
    contract = tmp_path / "contract.md"
    prompt.write_text("Review this.", encoding="utf-8")
    contract.write_bytes(CONTRACT)
    seen = []

    def fake_review(_store, selected, **kwargs):
        seen.append((selected, kwargs))
        return {"review": {"completed_call": True}, "response": "PASS: checked", "replayed": False}

    monkeypatch.setattr("clankops.hermes_bridge.review", fake_review)
    code = main(["--db", str(db_path), "--actor", "tester", "--json", "factory",
                 "hermes-review", mission, "--candidate-sha", SHA,
                 "--review-id", "review-1", "--checkout", str(tmp_path),
                 "--prompt-file", str(prompt), "--contract", str(contract)])
    assert code == 0
    assert seen[0][0] == mission
    assert seen[0][1]["review_id"] == "review-1"
    assert seen[0][1]["contract_file"] == contract
    assert json.loads(capsys.readouterr().out)["response"] == "PASS: checked"
