"""Event-backed Mission phases and acceptance attestations for local workers.

Hermes reports are evidence of a model run, never gate verification. Gate
references and bases are supplied by the caller; this module binds them to
the candidate but does not verify external evidence. The contract and phase
graph are frozen once per Mission.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from clankops.enums import EventSource, EventType, MissionState
from clankops.errors import DuplicateError, InvalidTransitionError, NotFoundError, ValidationError
from clankops.redact import sanitize_text
from clankops.store import Store

_SHA = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_PHASE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_BASIS = {EventSource.USER, EventSource.LOCAL_GIT, EventSource.GITHUB,
          EventSource.CI, EventSource.DEPLOYMENT, EventSource.SYSTEM}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _clean_ref(value: str) -> str:
    cleaned = sanitize_text(value.strip()) or ""
    if not cleaned or cleaned != value.strip() or len(cleaned) > 512:
        raise ValidationError("evidence ref is empty, too long, or contains a credential")
    return cleaned


def validate_manifest(manifest: dict[str, Any], mission_display: str, digest: str) -> None:
    if set(manifest) != {"schema_version", "mission", "contract_sha256", "start_phase",
                         "phases", "terminal_results", "gates", "transitions"}:
        raise ValidationError("factory manifest has missing or unknown fields")
    if manifest.get("schema_version") != 1 or manifest.get("mission") != mission_display:
        raise ValidationError("factory manifest version or Mission does not match")
    if manifest.get("contract_sha256") != digest or not _SHA.fullmatch(digest):
        raise ValidationError("factory contract hash does not match the file")
    phases = manifest.get("phases")
    if not isinstance(phases, list) or not phases or any(not isinstance(p, str) for p in phases):
        raise ValidationError("factory phases must be a unique nonempty list")
    if len(phases) != len(set(phases)):
        raise ValidationError("factory phases must be a unique nonempty list")
    if any(not isinstance(p, str) or not _PHASE.fullmatch(p) for p in phases):
        raise ValidationError("invalid factory phase")
    results = manifest.get("terminal_results")
    if (not isinstance(results, list) or set(results) !=
            {"PASS", "ROLLED_BACK", "HUMAN_REQUIRED", "FAILED"} or len(results) != 4):
        raise ValidationError("factory requires the four distinct terminal results")
    if ("CLOSED" not in phases or "PASS" in phases or
            manifest.get("start_phase") not in phases or manifest["start_phase"] == "CLOSED"):
        raise ValidationError("factory requires CLOSED and an explicit nonterminal start_phase")
    gates = manifest.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValidationError("factory requires acceptance gates")
    keys: set[str] = set()
    for gate in gates:
        if not isinstance(gate, dict) or not isinstance(gate.get("key"), str):
            raise ValidationError("invalid factory gate")
        if set(gate) != {"key", "scope", "description"}:
            raise ValidationError("factory gate has missing or unknown fields")
        key = gate["key"]
        if not _KEY.fullmatch(key) or key in keys or gate.get("scope") not in {"contract", "candidate"}:
            raise ValidationError(f"invalid or duplicate factory gate: {key}")
        if not isinstance(gate.get("description"), str) or not gate["description"].strip():
            raise ValidationError(f"factory gate needs a description: {key}")
        keys.add(key)
    edges = manifest.get("transitions")
    if not isinstance(edges, list) or not edges:
        raise ValidationError("factory requires transitions")
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValidationError("invalid factory transition")
        if set(edge) != {"from", "to", "requires"}:
            raise ValidationError("factory transition has missing or unknown fields")
        src, dst, required = edge.get("from"), edge.get("to"), edge.get("requires")
        if (src not in phases or dst not in phases or src == dst or
                not isinstance(required, list) or any(not isinstance(k, str) or k not in keys for k in required) or
                len(required) != len(set(required)) or (src, dst) in seen):
            raise ValidationError("invalid or duplicate factory transition")
        seen.add((src, dst))
    if any(edge["from"] == "CLOSED" for edge in edges):
        raise ValidationError("CLOSED is terminal")


def status(store: Store, mission: str) -> dict[str, Any]:
    row = store.resolve_mission(mission)
    state = store.conn.execute(
        "SELECT state_json FROM factory_missions WHERE mission_id = ?", (row["mission_id"],)
    ).fetchone()
    if state is None:
        raise NotFoundError(f"no factory contract for {row['display_id']}")
    result = json.loads(state[0])
    result["mission_state"] = row["state"]
    return result


def _write(store: Store, mission: str, event_type: EventType, change, *, actor: str | None = None,
           source: str | None = None) -> dict[str, Any]:
    store._begin_immediate()
    try:
        row = store.resolve_mission(mission)
        if row["state"] in {MissionState.COMPLETED, MissionState.ABANDONED, MissionState.SUPERSEDED}:
            raise InvalidTransitionError("factory Mission is terminal")
        state = status(store, row["mission_id"])
        del state["mission_state"]
        updated = change(deepcopy(state))
        store._emit(event_type, {"state": updated}, actor=actor, source=source,
                    clank_id=row["clank_id"], mission_id=row["mission_id"])
        store.commit()
        return status(store, row["mission_id"])
    except Exception:
        store.conn.rollback()
        raise


def freeze(store: Store, mission: str, manifest: dict[str, Any], contract: bytes,
           *, actor: str | None = None) -> dict[str, Any]:
    digest = hashlib.sha256(contract).hexdigest()
    row = store.resolve_mission(mission)
    validate_manifest(manifest, row["display_id"], digest)
    store._begin_immediate()
    try:
        row = store.resolve_mission(mission)
        if row["state"] in {MissionState.COMPLETED, MissionState.ABANDONED, MissionState.SUPERSEDED}:
            raise InvalidTransitionError("cannot freeze a terminal Mission")
        existing = store.conn.execute(
            "SELECT 1 FROM factory_missions WHERE mission_id = ?", (row["mission_id"],)
        ).fetchone()
        if existing:
            raise DuplicateError("factory contract is already frozen; use status")
        state = {
            "mission": row["display_id"], "mission_id": row["mission_id"],
            "contract_sha256": digest, "manifest_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
            "manifest": manifest, "phase": manifest["start_phase"],
            "candidate_sha": None, "gate_results": {}, "last_review": None,
            "terminal_result": None,
        }
        store._emit(EventType.FACTORY_FROZEN, {"state": state}, actor=actor,
                    clank_id=row["clank_id"], mission_id=row["mission_id"])
        store.commit()
        return status(store, row["mission_id"])
    except Exception:
        store.conn.rollback()
        raise


def select_candidate(store: Store, mission: str, sha: str, *, actor: str | None = None) -> dict[str, Any]:
    if not _COMMIT.fullmatch(sha):
        raise ValidationError("candidate SHA must be 40 lowercase hex characters")

    def change(state: dict[str, Any]) -> dict[str, Any]:
        if state["phase"] not in {"BUILD", "REWORK", "TEST", "REVIEW"}:
            raise InvalidTransitionError("candidate can be selected only in BUILD/REWORK/TEST/REVIEW")
        if state["candidate_sha"] == sha:
            return state
        if state["phase"] == "REVIEW" and state["candidate_sha"] is not None:
            raise InvalidTransitionError("enter REWORK before replacing a reviewed candidate")
        state["candidate_sha"] = sha
        state["last_review"] = None
        # Contract-scoped attestations remain; candidate-scoped evidence belongs to the old SHA.
        scopes = {g["key"]: g["scope"] for g in state["manifest"]["gates"]}
        state["gate_results"] = {k: v for k, v in state["gate_results"].items()
                                 if scopes[k] == "contract"}
        return state

    return _write(store, mission, EventType.FACTORY_CANDIDATE_SELECTED, change, actor=actor)


def record_gate(store: Store, mission: str, key: str, verdict: str, evidence_ref: str,
                basis: str, *, actor: str | None = None) -> dict[str, Any]:
    if verdict not in {"PASS", "FAIL"}:
        raise ValidationError("gate verdict must be PASS or FAIL")
    evidence_ref = _clean_ref(evidence_ref)
    if basis not in {s.value for s in _BASIS}:
        raise ValidationError("gate basis must be USER, LOCAL_GIT, GITHUB, CI, DEPLOYMENT, or SYSTEM")

    def change(state: dict[str, Any]) -> dict[str, Any]:
        gate = next((g for g in state["manifest"]["gates"] if g["key"] == key), None)
        if gate is None:
            raise ValidationError(f"unknown frozen gate: {key}")
        if state["terminal_result"] is not None:
            raise InvalidTransitionError("factory terminal result is already recorded")
        if gate["scope"] == "candidate" and not state["candidate_sha"]:
            raise ValidationError("select the candidate SHA before recording this gate")
        state["gate_results"][key] = {
            "verdict": verdict, "evidence_ref": evidence_ref, "basis": basis,
            "candidate_sha": state["candidate_sha"] if gate["scope"] == "candidate" else None,
            "actor": actor or store.default_actor,
        }
        return state

    return _write(store, mission, EventType.FACTORY_GATE_RECORDED, change, actor=actor)


def advance(store: Store, mission: str, target: str, *, expect_phase: str,
            expect_candidate: str | None = None, actor: str | None = None) -> dict[str, Any]:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        if state["phase"] != expect_phase or (expect_candidate is not None and
                                            state["candidate_sha"] != expect_candidate):
            raise InvalidTransitionError("factory phase or candidate changed; refresh status")
        edge = next((e for e in state["manifest"]["transitions"]
                     if e["from"] == expect_phase and e["to"] == target), None)
        if edge is None:
            raise InvalidTransitionError(f"transition {expect_phase} -> {target} is not frozen")
        gates = {g["key"]: g for g in state["manifest"]["gates"]}
        required = set(edge["requires"])
        if target in {"ACCEPTANCE", "CLOSED"}:
            if not state["candidate_sha"]:
                raise ValidationError("ACCEPTANCE/CLOSED requires an exact candidate SHA")
            review = state.get("last_review") or {}
            if (review.get("candidate_sha") != state["candidate_sha"] or
                    review.get("reported_verdict") != "PASS" or
                    review.get("completed_call") is not True):
                raise InvalidTransitionError("ACCEPTANCE/CLOSED requires a completed Hermes review of this candidate")
        if target == "CLOSED":
            required.update(gates)
        for key in required:
            result = state["gate_results"].get(key) or {}
            if result.get("verdict") != "PASS" or (gates[key]["scope"] == "candidate" and
                result.get("candidate_sha") != state["candidate_sha"]):
                raise InvalidTransitionError(f"factory gate is not PASS for this candidate: {key}")
        state["phase"] = target
        if target == "CLOSED":
            state["terminal_result"] = "PASS"
        elif target in {"ROLLED_BACK", "HUMAN_REQUIRED", "FAILED"}:
            state["terminal_result"] = target
        return state

    return _write(store, mission, EventType.FACTORY_PHASE_ADVANCED, change, actor=actor)


def record_review(store: Store, mission: str, review: dict[str, Any],
                  *, actor: str | None = None) -> dict[str, Any]:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        if state["phase"] != "REVIEW" or review["candidate_sha"] != state["candidate_sha"]:
            raise InvalidTransitionError("review must match the current REVIEW candidate")
        state["last_review"] = review
        return state

    return _write(store, mission, EventType.FACTORY_HERMES_REVIEW_RECORDED, change,
                  actor=actor, source=EventSource.AGENT_REPORT)
