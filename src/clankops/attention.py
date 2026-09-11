"""Derived attention queue. Read-only. Writes zero ledger events.

Freshness is evidence metadata, not truth. Old != wrong.
Deployed SHA != source HEAD is informational, not failure.
Unknown evidence never becomes a negative assertion.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from clankops.ci import CI_ARTIFACT_KIND
from clankops.deployment import current_deployments
from clankops.enums import EventSource
from clankops.errors import NotFoundError
from clankops.readmodel import DEFAULT_STALE, open_sessions
from clankops.reconcile import heads_match, reconcile_clank, working_tree_kind
from clankops.store import Store
from clankops.timefmt import format_age, parse_utc, short_head

REASON_MISSION_NO_NEXT_ACTION = "MISSION_NO_NEXT_ACTION"
REASON_STALE_OPEN_SESSION = "STALE_OPEN_SESSION"
REASON_GIT_DRIFT = "GIT_DRIFT"
REASON_DIRTY_WITHOUT_OPEN_SESSION = "DIRTY_WITHOUT_OPEN_SESSION"
REASON_CI_EVIDENCE_BEHIND_MISSION = "CI_EVIDENCE_BEHIND_MISSION"
REASON_DEPLOYMENT_DIFFERS_FROM_MISSION = "DEPLOYMENT_DIFFERS_FROM_MISSION"

CLASS_INTEGRITY = "integrity"
CLASS_INFORMATIONAL = "informational"
CLASS_AGE = "age"

CLASS_MARK = {
    CLASS_INTEGRITY: "■",
    CLASS_INFORMATIONAL: "◇",
    CLASS_AGE: "○",
}

_CLASS_ORDER = {
    CLASS_INTEGRITY: 0,
    CLASS_INFORMATIONAL: 1,
    CLASS_AGE: 2,
}
_CODE_ORDER = {
    REASON_MISSION_NO_NEXT_ACTION: 0,
    REASON_GIT_DRIFT: 1,
    REASON_DIRTY_WITHOUT_OPEN_SESSION: 2,
    REASON_CI_EVIDENCE_BEHIND_MISSION: 3,
    REASON_DEPLOYMENT_DIFFERS_FROM_MISSION: 4,
    REASON_STALE_OPEN_SESSION: 5,
}

DEFAULT_THRESHOLD_LABEL = "24h"
DEFAULT_THRESHOLD_SOURCE = "session-staleness default"
OPERATOR_THRESHOLD_SOURCE = "operator-supplied"


def _item(
    *,
    clank: dict[str, Any],
    mission: dict[str, Any] | None,
    reason_code: str,
    reason: str,
    item_class: str,
    timestamp: str | None,
    age: str | None,
    evidence: dict[str, Any],
    suggested_action: str,
    source: str,
    provenance: list[str],
    threshold: str | None = None,
    threshold_source: str | None = None,
) -> dict[str, Any]:
    row = {
        "clank": clank.get("slug"),
        "clank_id": clank.get("clank_id"),
        "clank_name": clank.get("display_name"),
        "mission": mission["display_id"] if mission else None,
        "mission_id": mission["mission_id"] if mission else None,
        "mission_state": mission["state"] if mission else None,
        "reason_code": reason_code,
        "reason": reason,
        "class": item_class,
        "timestamp": timestamp,
        "age": age,
        "evidence": evidence,
        "suggested_action": suggested_action,
        "source": str(source),
        "provenance": [str(part) for part in provenance if part],
    }
    if threshold is not None:
        row["threshold"] = threshold
        row["threshold_source"] = threshold_source
    return row


def _age(now: datetime, ts: str | None) -> str | None:
    instant = parse_utc(ts)
    if instant is None:
        return None
    return format_age(now - instant)


def _recorded_next_action(checkpoint: dict[str, Any] | None) -> str | None:
    if not checkpoint:
        return None
    value = checkpoint.get("next_action")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json") or row.get("metadata") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _latest_ci_artefact(store: Store, mission_id: str) -> dict[str, Any] | None:
    row = store.conn.execute(
        """
        SELECT * FROM artifacts
        WHERE mission_id = ? AND kind = ?
        ORDER BY created_utc DESC, artifact_id DESC
        LIMIT 1
        """,
        (mission_id, CI_ARTIFACT_KIND),
    ).fetchone()
    return dict(row) if row else None


def _ci_sha(row: dict[str, Any]) -> str | None:
    meta = _meta(row)
    return meta.get("sha") or row.get("ref")


def _sessions_for_clank(open_rows: list[dict[str, Any]], clank_id: str) -> list[dict[str, Any]]:
    return [row for row in open_rows if row.get("clank_id") == clank_id]


def _mission_from_reconcile(store: Store, rec: dict[str, Any]) -> dict[str, Any] | None:
    """Mission Foundation 3 actually compared. Never a silent unfinished[0] pick."""
    display = rec.get("mission_display")
    if not display:
        return None
    try:
        return store.resolve_mission(display)
    except NotFoundError:
        return None


def _format_drift_value(field: str, value: Any) -> str:
    if field == "head":
        return short_head(value) or "unknown"
    if value is None or value == "":
        return "unknown"
    return str(value)


def _mission_no_next_action(
    clank: dict[str, Any],
    mission: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any] | None:
    if _recorded_next_action(checkpoint):
        return None
    ts = checkpoint["recorded_utc"] if checkpoint else mission.get("created_utc")
    return _item(
        clank=clank,
        mission=mission,
        reason_code=REASON_MISSION_NO_NEXT_ACTION,
        reason=f"{mission['display_id']} has no recorded next action",
        item_class=CLASS_INTEGRITY,
        timestamp=ts,
        age=_age(now, ts),
        evidence={
            "checkpoint_id": checkpoint.get("checkpoint_id") if checkpoint else None,
            "checkpoint_utc": checkpoint.get("recorded_utc") if checkpoint else None,
        },
        suggested_action=(
            "Record a next action on the unfinished Mission when one is known; "
            "do not invent one"
        ),
        source=str(checkpoint.get("source") if checkpoint and checkpoint.get("source") else EventSource.SYSTEM),
        provenance=[
            str(checkpoint.get("source")) if checkpoint and checkpoint.get("source") else str(EventSource.SYSTEM)
        ],
    )


def _stale_session_item(
    clank: dict[str, Any],
    session: dict[str, Any],
    now: datetime,
    *,
    threshold_label: str,
    threshold_source: str,
) -> dict[str, Any]:
    mission = None
    if session.get("mission_display"):
        mission = {
            "display_id": session.get("mission_display"),
            "mission_id": session.get("mission_id"),
            "state": session.get("mission_state"),
        }
    return _item(
        clank=clank,
        mission=mission,
        reason_code=REASON_STALE_OPEN_SESSION,
        reason=(
            f"open Session {session.get('session_id')} age {session.get('age') or 'unknown'} "
            f"exceeds {threshold_label} ({threshold_source})"
        ),
        item_class=CLASS_AGE,
        timestamp=session.get("started_utc"),
        age=session.get("age"),
        evidence={
            "session_id": session.get("session_id"),
            "started_utc": session.get("started_utc"),
            "age_seconds": session.get("age_seconds"),
        },
        suggested_action="Review the open Session; do not auto-close it",
        source=str(EventSource.SYSTEM),
        provenance=[str(EventSource.SYSTEM)],
        threshold=threshold_label,
        threshold_source=threshold_source,
    )


def _git_drift_items(
    clank: dict[str, Any],
    mission: dict[str, Any] | None,
    rec: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    if rec.get("status") != "drift":
        return []
    recorded = rec.get("recorded") or {}
    items: list[dict[str, Any]] = []
    for row in rec.get("drift") or []:
        field = row.get("field") or "unknown"
        rec_val = _format_drift_value(field, row.get("recorded"))
        obs_val = _format_drift_value(field, row.get("observed"))
        source = str(row.get("source") or EventSource.LOCAL_GIT)
        items.append(
            _item(
                clank=clank,
                mission=mission,
                reason_code=REASON_GIT_DRIFT,
                reason=f"recorded {field} {rec_val} != observed {obs_val}",
                item_class=CLASS_INTEGRITY,
                timestamp=recorded.get("checkpoint_utc"),
                age=_age(now, recorded.get("checkpoint_utc")),
                evidence={
                    "field": field,
                    "recorded": row.get("recorded"),
                    "observed": row.get("observed"),
                    "source": source,
                    "checkpoint_id": recorded.get("checkpoint_id"),
                    "reconcile_status": rec.get("status"),
                },
                suggested_action="Inspect recorded vs observed git claims; reconcile stays read-only",
                source=source,
                provenance=[source],
            )
        )
    return items


def _dirty_without_session_item(
    clank: dict[str, Any],
    mission: dict[str, Any] | None,
    rec: dict[str, Any],
    open_for_clank: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any] | None:
    del now
    if open_for_clank:
        return None
    local = rec.get("observed_local") or {}
    if not local.get("ok"):
        return None
    if working_tree_kind(local.get("working_tree")) != "dirty" and local.get("dirty") is not True:
        return None
    return _item(
        clank=clank,
        mission=mission,
        reason_code=REASON_DIRTY_WITHOUT_OPEN_SESSION,
        reason=(
            f"locally observable checkout is dirty with no open Session "
            f"({local.get('working_tree') or 'dirty'})"
        ),
        item_class=CLASS_INTEGRITY,
        timestamp=None,
        age=None,
        evidence={
            "path": local.get("path"),
            "working_tree": local.get("working_tree"),
            "head": local.get("head"),
        },
        suggested_action=(
            "Open a Session before mutating this checkout, or record the dirty tree as evidence"
        ),
        source=str(EventSource.LOCAL_GIT),
        provenance=[str(EventSource.LOCAL_GIT)],
    )


def _ci_behind_item(
    store: Store,
    clank: dict[str, Any],
    mission: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any] | None:
    recorded_head = checkpoint.get("head") if checkpoint else None
    if not recorded_head:
        return None
    artefact = _latest_ci_artefact(store, mission["mission_id"])
    if artefact is None:
        return None
    artefact_sha = _ci_sha(artefact)
    if not artefact_sha:
        return None
    if heads_match(recorded_head, artefact_sha) is True:
        return None
    ts = artefact.get("created_utc")
    return _item(
        clank=clank,
        mission=mission,
        reason_code=REASON_CI_EVIDENCE_BEHIND_MISSION,
        reason=(
            f"latest github_ci artefact sha {short_head(artefact_sha) or 'unknown'} "
            f"differs from Mission recorded HEAD {short_head(recorded_head) or 'unknown'}"
        ),
        item_class=CLASS_INTEGRITY,
        timestamp=ts,
        age=_age(now, ts),
        evidence={
            "artifact_id": artefact.get("artifact_id"),
            "artefact_sha": artefact_sha,
            "recorded_head": recorded_head,
            "captured_utc": ts,
        },
        suggested_action="Capture GitHub CI for the Mission's current recorded HEAD",
        source=str(EventSource.CI),
        provenance=[str(EventSource.CI)],
    )


def _deployment_differs_items(
    store: Store,
    clank: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Compare each current surface only with the Mission that observation named."""
    items: list[dict[str, Any]] = []
    for row in current_deployments(store, clank["clank_id"], now=now):
        mission_id = row.get("mission_id")
        if not mission_id:
            continue
        try:
            mission = store.resolve_mission(mission_id)
        except NotFoundError:
            continue
        if mission.get("clank_id") != clank.get("clank_id"):
            continue
        checkpoint = store.latest_checkpoint(clank["clank_id"], mission["mission_id"])
        recorded_head = checkpoint.get("head") if checkpoint else None
        if not recorded_head:
            continue
        deployed = row.get("deployed_sha")
        if not deployed:
            continue
        if heads_match(recorded_head, deployed) is True:
            continue
        ts = row.get("observed_at") or row.get("created_utc")
        surface = row.get("surface_id") or "unknown"
        items.append(
            _item(
                clank=clank,
                mission=mission,
                reason_code=REASON_DEPLOYMENT_DIFFERS_FROM_MISSION,
                reason=(
                    f"deployment differs from recorded Mission HEAD: "
                    f"{surface} deployed {short_head(deployed) or 'unknown'}; "
                    f"Mission recorded {short_head(recorded_head) or 'unknown'}"
                ),
                item_class=CLASS_INFORMATIONAL,
                timestamp=ts,
                age=_age(now, ts),
                evidence={
                    "surface_id": row.get("surface_id"),
                    "observation_id": row.get("observation_id"),
                    "deployed_sha": deployed,
                    "recorded_head": recorded_head,
                    "host_identity": row.get("host_identity"),
                    "environment": row.get("environment"),
                    "mission_id": mission["mission_id"],
                },
                suggested_action=(
                    "Informational only: deployed SHA differs from recorded Mission HEAD. "
                    "Not a failure and not automatically stale"
                ),
                source=str(EventSource.DEPLOYMENT),
                provenance=[str(EventSource.DEPLOYMENT)],
            )
        )
    return items


def _freshness(
    store: Store,
    clank: dict[str, Any],
    unfinished: list[dict[str, Any]],
    open_for: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    sessions = [
        {
            "session_id": row.get("session_id"),
            "mission_id": row.get("mission_id"),
            "mission_display": row.get("mission_display"),
            "started_utc": row.get("started_utc"),
            "age": row.get("age"),
            "age_seconds": row.get("age_seconds"),
        }
        for row in open_for
    ]
    sessions.sort(key=lambda row: (row.get("started_utc") or "", row.get("session_id") or ""))
    single = sessions[0] if len(sessions) == 1 else None
    deployments = []
    for row in current_deployments(store, clank["clank_id"], now=now):
        ts = row.get("observed_at") or row.get("created_utc")
        deployments.append(
            {
                "surface_id": row.get("surface_id"),
                "observation_id": row.get("observation_id"),
                "observed_at": ts,
                "age": row.get("age") or _age(now, ts),
            }
        )
    missions = []
    for mission in unfinished:
        checkpoint = store.latest_checkpoint(clank["clank_id"], mission["mission_id"])
        artefact = _latest_ci_artefact(store, mission["mission_id"])
        missions.append(
            {
                "mission": mission["display_id"],
                "mission_id": mission["mission_id"],
                "checkpoint_utc": checkpoint.get("recorded_utc") if checkpoint else None,
                "checkpoint_age": _age(now, checkpoint.get("recorded_utc") if checkpoint else None),
                "ci_capture_utc": artefact.get("created_utc") if artefact else None,
                "ci_capture_age": _age(now, artefact.get("created_utc") if artefact else None),
            }
        )
    return {
        "clank": clank.get("slug"),
        "clank_id": clank.get("clank_id"),
        "open_sessions": sessions,
        "open_session_id": single.get("session_id") if single else None,
        "open_session_utc": single.get("started_utc") if single else None,
        "open_session_age": single.get("age") if single else None,
        "missions": missions,
        "deployments": deployments,
    }


def _sort_key(item: dict[str, Any]) -> tuple:
    ts = item.get("timestamp") or "9999"
    return (
        _CLASS_ORDER.get(item.get("class") or "", 9),
        ts,
        _CODE_ORDER.get(item.get("reason_code") or "", 9),
        item.get("clank") or "",
        item.get("mission") or "",
        (item.get("evidence") or {}).get("surface_id") or "",
        (item.get("evidence") or {}).get("field") or "",
    )


def attention_report(
    store: Store,
    clank: str | None = None,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    threshold_label: str = DEFAULT_THRESHOLD_LABEL,
    threshold_source: str = DEFAULT_THRESHOLD_SOURCE,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
) -> dict[str, Any]:
    """Derived attention items. Never mutates the ledger."""
    instant = now or store.clock.now()
    open_rows = open_sessions(store, now=instant, stale_after=stale_after)
    targets = [store.resolve_clank(clank)] if clank else store.list_clanks()
    items: list[dict[str, Any]] = []
    freshness: list[dict[str, Any]] = []
    for row in targets:
        clank_row = dict(row)
        unfinished = store.unfinished_missions(clank_row["clank_id"])
        rec = reconcile_clank(
            store,
            clank_row["slug"],
            include_github=include_github,
            inspect_local=inspect_local,
            inspect_remote=inspect_remote,
        )
        rec_mission = _mission_from_reconcile(store, rec)
        open_for = _sessions_for_clank(open_rows, clank_row["clank_id"])
        freshness.append(_freshness(store, clank_row, unfinished, open_for, instant))
        for mission in unfinished:
            checkpoint = store.latest_checkpoint(clank_row["clank_id"], mission["mission_id"])
            item = _mission_no_next_action(clank_row, mission, checkpoint, instant)
            if item:
                items.append(item)
            ci_item = _ci_behind_item(store, clank_row, mission, checkpoint, instant)
            if ci_item:
                items.append(ci_item)
        items.extend(_deployment_differs_items(store, clank_row, instant))
        items.extend(_git_drift_items(clank_row, rec_mission, rec, instant))
        dirty = _dirty_without_session_item(clank_row, rec_mission, rec, open_for, instant)
        if dirty:
            items.append(dirty)
        for session in open_for:
            if session.get("stale"):
                items.append(
                    _stale_session_item(
                        clank_row,
                        session,
                        instant,
                        threshold_label=threshold_label,
                        threshold_source=threshold_source,
                    )
                )
    items.sort(key=_sort_key)
    return {
        "items": items,
        "freshness": freshness,
        "threshold": threshold_label,
        "threshold_source": threshold_source,
        "derived": True,
        "authoritative": False,
    }


def format_attention_text(report: dict[str, Any]) -> str:
    lines = [
        "ATTENTION (derived; not authoritative; writes zero ledger events)",
        f"threshold {report.get('threshold')} ({report.get('threshold_source')})",
        "",
    ]
    items = report.get("items") or []
    if not items:
        lines.append("none derived")
        return "\n".join(lines)
    for item in items:
        mark = CLASS_MARK.get(item.get("class") or "", "·")
        name = item.get("clank_name") or item.get("clank") or "unknown"
        mission = item.get("mission") or "no mission"
        lines.append(f"{mark} {name}")
        lines.append(f"  {item.get('reason_code')}")
        lines.append(f"  {item.get('reason')}")
        lines.append(
            f"  mission {mission}  age {item.get('age') or 'unknown'}  "
            f"source {item.get('source') or 'unknown'}"
        )
        evidence = item.get("evidence") or {}
        bits = []
        for key in (
            "field",
            "checkpoint_id",
            "session_id",
            "artifact_id",
            "observation_id",
            "surface_id",
            "recorded",
            "observed",
            "artefact_sha",
            "deployed_sha",
        ):
            value = evidence.get(key)
            if value:
                bits.append(f"{key}={value}")
        if bits:
            lines.append("  evidence " + " ".join(bits))
        if item.get("suggested_action"):
            lines.append(f"  action {item['suggested_action']}")
        provenance = item.get("provenance") or []
        if provenance:
            lines.append("  provenance " + ", ".join(str(part) for part in provenance))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
