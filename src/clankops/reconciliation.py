"""Explicit Mission state reconciliation from evidence.

Corrects present projected knowledge. Does not rewrite history, fabricate
an ACTIVE interval, create a Session, or pretend ClankOps observed the
completion live. Ordinary Mission transitions remain unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from clankops.clock import isoformat_utc
from clankops.enums import EventSource, EventType, MissionState
from clankops.errors import InvalidTransitionError, ValidationError
from clankops.ids import new_id
from clankops.store import Store
from clankops.timefmt import parse_utc

RECORDER = "clankops.reconciliation"
ALLOWED_FROM = frozenset(
    {MissionState.PLANNED, MissionState.PAUSED, MissionState.BLOCKED}
)
ALLOWED_TO = frozenset({MissionState.COMPLETED})
RECONCILIATION_SOURCE = EventSource.USER
EVIDENCE_PLANE_SOURCES = frozenset(
    {
        EventSource.GITHUB,
        EventSource.CI,
        EventSource.DEPLOYMENT,
        EventSource.LOCAL_GIT,
    }
)
RECONCILIATION_BASES = frozenset(
    {
        "operator-confirmed",
        "github",
        "ci",
        "deployment",
        "reconstructed",
        "local_git",
        "artefact",
    }
)
_KIND_ALIASES = {
    ("github", "pr"): ("github_pr", EventSource.GITHUB),
    ("github", "commit"): ("github_merge_sha", EventSource.GITHUB),
    ("github", "sha"): ("github_merge_sha", EventSource.GITHUB),
    ("github", "url"): ("github_url", EventSource.GITHUB),
    ("ci", "run"): ("ci_run", EventSource.CI),
    ("artefact",): ("artefact_id", EventSource.SYSTEM),
    ("artifact",): ("artefact_id", EventSource.SYSTEM),
}


def parse_evidence_item(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValidationError("evidence item is empty")
    parts = text.split(":", 2)
    if len(parts) == 1:
        raise ValidationError(
            "evidence must look like github:pr:6 or github:commit:<sha>"
        )
    if len(parts) == 2:
        kind, value = parts[0].strip().lower(), parts[1].strip()
        mapped = _KIND_ALIASES.get((kind,))
        if mapped is None:
            raise ValidationError(f"unrecognised evidence kind: {kind}")
        field, source = mapped
    else:
        family, subtype, value = (
            parts[0].strip().lower(),
            parts[1].strip().lower(),
            parts[2].strip(),
        )
        mapped = _KIND_ALIASES.get((family, subtype))
        if mapped is None:
            raise ValidationError(f"unrecognised evidence kind: {family}:{subtype}")
        field, source = mapped
    if not value:
        raise ValidationError("evidence value is required")
    return {
        "kind": field,
        "value": value,
        "source": str(source),
    }


def format_evidence_labels(items: Sequence[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for item in items or []:
        kind = str(item.get("kind") or "")
        value = str(item.get("value") or "")
        if kind == "github_pr":
            parts.append(f"PR #{value}")
        elif kind == "github_merge_sha":
            parts.append(value[:12] if len(value) > 12 else value)
        elif kind == "github_url":
            parts.append(value)
        else:
            parts.append(f"{kind}={value}" if kind else value)
    return " / ".join(part for part in parts if part)


def parse_evidence(items: Sequence[str] | None) -> list[dict[str, Any]]:
    rows = [parse_evidence_item(item) for item in (items or [])]
    if not rows:
        raise ValidationError("reconciliation requires at least one --evidence item")
    return rows


def reconciliations_for_mission(store: Store, mission_id: str) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        """
        SELECT r.*, m.display_id AS mission_display
        FROM mission_reconciliations r
        LEFT JOIN missions m ON m.mission_id = r.mission_id
        WHERE r.mission_id = ?
        ORDER BY r.ledger_seq, r.reconciliation_id
        """,
        (mission_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def reconciliations_for_clank(store: Store, clank_id: str) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        """
        SELECT r.*, m.display_id AS mission_display
        FROM mission_reconciliations r
        LEFT JOIN missions m ON m.mission_id = r.mission_id
        WHERE r.clank_id = ?
        ORDER BY r.ledger_seq, r.reconciliation_id
        """,
        (clank_id,),
    ).fetchall()
    return [_fact(row) for row in rows]


def _fact(row: dict[str, Any] | Any) -> dict[str, Any]:
    data = dict(row)
    evidence = data.get("evidence_json")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = []
    return {
        "reconciliation_id": data.get("reconciliation_id"),
        "mission_id": data.get("mission_id"),
        "mission_display": data.get("mission_display"),
        "clank_id": data.get("clank_id"),
        "from_state": data.get("from_state"),
        "to_state": data.get("to_state"),
        "reason": data.get("reason"),
        "evidence": evidence or [],
        "evidence_occurred_at": data.get("evidence_occurred_at"),
        "reconciliation_basis": data.get("reconciliation_basis"),
        "actor": data.get("actor"),
        "source": data.get("source"),
        "observed_at": data.get("observed_at"),
        "ledger_seq": data.get("ledger_seq"),
    }


def _require_user_source(source: str | EventSource | None) -> EventSource:
    """Reconciliation is a ClankOps operator action. Evidence planes are not the event source."""
    if source is None or str(source).strip() == "":
        return RECONCILIATION_SOURCE
    text = str(source).strip()
    if text != EventSource.USER:
        plane = (
            "evidence plane"
            if text in {str(item) for item in EVIDENCE_PLANE_SOURCES}
            else "non-USER source"
        )
        raise ValidationError(
            f"reconciliation event source must be USER; {text} is a {plane}, "
            "not the reconciliation action. Put GitHub/CI/deployment/local git on evidence[].source."
        )
    return RECONCILIATION_SOURCE


def _open_session_ids(store: Store, mission_id: str) -> list[str]:
    rows = store.conn.execute(
        """
        SELECT session_id FROM sessions
        WHERE mission_id = ? AND ended_utc IS NULL
        ORDER BY started_utc, session_id
        """,
        (mission_id,),
    ).fetchall()
    return [row["session_id"] for row in rows]


def latest_reconciliation(store: Store, mission_id: str) -> dict[str, Any] | None:
    rows = reconciliations_for_mission(store, mission_id)
    return _fact(rows[-1]) if rows else None


def reconcile_mission(
    store: Store,
    mission: str,
    *,
    to_state: str,
    reason: str,
    evidence: Sequence[str],
    basis: str,
    actor: str | None = None,
    source: str | EventSource | None = None,
    evidence_occurred_at: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Correct projected Mission state from explicit evidence. Never rewrites events."""
    source_val = _require_user_source(source)
    why = (reason or "").strip()
    if not why:
        raise ValidationError("reconciliation requires an explicit --reason")
    basis_text = (basis or "").strip().lower()
    if basis_text not in RECONCILIATION_BASES:
        raise ValidationError(
            "reconciliation --basis must be one of: "
            + ", ".join(sorted(RECONCILIATION_BASES))
        )
    target = MissionState(str(to_state).strip())
    if target not in ALLOWED_TO:
        raise InvalidTransitionError(
            f"Foundation 11 reconciliation can only target COMPLETED, not {target}"
        )
    items = parse_evidence(evidence)
    occurred = None
    if evidence_occurred_at:
        parsed = parse_utc(evidence_occurred_at)
        if parsed is None:
            raise ValidationError("evidence-occurred-at must be an ISO-8601 UTC timestamp")
        occurred = isoformat_utc(parsed)
    actor_name = (actor or store.default_actor or "").strip()
    if not actor_name:
        raise ValidationError("reconciliation actor is required")

    token_row = store.resolve_mission(mission)
    store._begin_immediate()
    event = None
    current = None
    reconciliation_id = None
    observed_at = None
    try:
        row = store.resolve_mission(token_row["mission_id"])
        current = MissionState(row["state"])
        if current not in ALLOWED_FROM:
            raise InvalidTransitionError(
                f"cannot reconcile mission {row['display_id']} from {current} to {target}; "
                "ordinary transitions remain for ACTIVE work, and terminal history is not rewritten"
            )
        open_ids = _open_session_ids(store, row["mission_id"])
        if open_ids:
            listed = ", ".join(open_ids)
            raise ValidationError(
                f"cannot reconcile {row['display_id']} around a live/open Session: {listed}"
            )
        reconciliation_id = new_id()
        observed_at = isoformat_utc(store.clock.now())
        payload = {
            "reconciliation_id": reconciliation_id,
            "mission_id": row["mission_id"],
            "clank_id": row["clank_id"],
            "from_state": str(current),
            "to_state": str(target),
            "reason": why,
            "evidence": items,
            "evidence_occurred_at": occurred,
            "reconciliation_basis": basis_text,
            "observed_at": observed_at,
        }
        event = store._emit(
            EventType.MISSION_STATE_RECONCILED,
            payload,
            actor=actor_name,
            source=source_val,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=None,
            provenance={
                "recorder": RECORDER,
                "authorised_by": actor_name,
                "evidence_sources": sorted({item["source"] for item in items}),
                "reconciliation_basis": basis_text,
            },
            bind_session=False,
        )
        if commit:
            store.commit()
    except Exception:
        store.conn.rollback()
        raise
    return {
        "mission": store.resolve_mission(token_row["mission_id"]),
        "reconciliation_id": reconciliation_id,
        "event_id": event.event_id,
        "ledger_seq": event.ledger_seq,
        "from_state": str(current),
        "to_state": str(target),
        "reason": why,
        "evidence": items,
        "evidence_occurred_at": occurred,
        "reconciliation_basis": basis_text,
        "observed_at": observed_at,
        "session_id": event.session_id,
    }
