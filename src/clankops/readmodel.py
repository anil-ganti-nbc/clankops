"""Read-only fleet coverage, session observability, and Terminal payloads."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from clankops.enums import CensusClassification, EventSource, EventType, MissionState
from clankops.errors import ValidationError
from clankops.events import list_events, list_recent_events
from clankops.process import session_process_view
from clankops.reconcile import reconcile_clank
from clankops.store import Store
from clankops.timefmt import format_age, parse_utc, short_head

DEFAULT_STALE = timedelta(hours=24)
TIMELINE_DEFAULT = 200
TIMELINE_MAX = 1000
CLANKOPS_SLUG = "clankops"
MISSION_SORT = {
    MissionState.ACTIVE: 0,
    MissionState.BLOCKED: 1,
    MissionState.PAUSED: 2,
    MissionState.PLANNED: 3,
}


def _now(store: Store, now: datetime | None = None) -> datetime:
    return now or store.clock.now()


def _normalize_remote(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().rstrip("/").lower()
    if text.endswith(".git"):
        text = text[:-4]
    return text or None


def coverage_report(store: Store, census: dict[str, Any]) -> dict[str, Any]:
    """Census vs ledger. Does not mutate. ClankOps is not a VERIFIED denominator."""
    candidates = list(census.get("candidates") or [])
    counts = {str(kind): 0 for kind in CensusClassification}
    by_class: dict[str, list[dict[str, Any]]] = {str(kind): [] for kind in CensusClassification}
    for cand in candidates:
        klass = str(cand.get("classification") or CensusClassification.UNKNOWN)
        counts[klass] = counts.get(klass, 0) + 1
        by_class.setdefault(klass, []).append(cand)

    registered = store.list_clanks()
    slug_index = {row["slug"]: row for row in registered}
    alias_index: dict[str, dict[str, Any]] = {}
    remote_index: dict[str, dict[str, Any]] = {}
    for row in registered:
        detail = store.clank_detail(row["clank_id"])
        for alias in detail.get("aliases") or []:
            alias_index[str(alias)] = row
        for ref in detail.get("refs") or []:
            if ref["ref_kind"] in {"git_remote", "github_repo"}:
                key = _normalize_remote(ref["ref_value"])
                if key:
                    remote_index[key] = row

    def _match(cand: dict[str, Any]) -> dict[str, Any] | None:
        slug = cand.get("slug") or cand.get("name")
        if slug and slug in slug_index:
            return slug_index[slug]
        if slug and slug in alias_index:
            return alias_index[slug]
        remotes: list[str] = []
        if cand.get("canonical_remote"):
            remotes.append(str(cand["canonical_remote"]))
        if cand.get("remote"):
            remotes.append(str(cand["remote"]))
        raw_remotes = cand.get("remotes") or []
        if isinstance(raw_remotes, dict):
            remotes.extend(str(v) for v in raw_remotes.values() if v)
        elif isinstance(raw_remotes, list):
            remotes.extend(str(v) for v in raw_remotes if v)
        for remote in remotes:
            key = _normalize_remote(remote)
            if key and key in remote_index:
                return remote_index[key]
        return None

    verified = by_class.get(CensusClassification.VERIFIED, [])
    verified_hits: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for cand in verified:
        hit = _match(cand)
        if hit:
            verified_hits.append(
                {"slug": cand.get("slug"), "clank_id": hit["clank_id"], "registered_slug": hit["slug"]}
            )
        else:
            unresolved.append({"slug": cand.get("slug"), "remote": cand.get("canonical_remote")})

    duplicates = []
    for group in census.get("duplicates") or []:
        duplicates.append(
            {
                "identity": group.get("identity"),
                "paths": list(group.get("paths") or []),
                "path_count": len(group.get("paths") or []),
            }
        )

    return {
        "census_candidates": len(candidates),
        "registered_clanks": len(registered),
        "verified_candidates": len(verified),
        "verified_registered": len(verified_hits),
        "verified_unresolved": unresolved,
        "verified_unresolved_count": len(unresolved),
        "probable": counts.get(CensusClassification.PROBABLE, 0),
        "unknown": counts.get(CensusClassification.UNKNOWN, 0),
        "support_component": counts.get(CensusClassification.SUPPORT_COMPONENT, 0),
        "not_a_clank": counts.get(CensusClassification.NOT_A_CLANK, 0),
        "needs_reconstruction": counts.get(CensusClassification.NEEDS_RECONSTRUCTION, 0),
        "classification_counts": counts,
        "duplicates": duplicates,
        "duplicate_identity_groups": len(duplicates),
        "registered_includes_clankops": CLANKOPS_SLUG in slug_index,
        "verified_denominator_note": (
            "VERIFIED counts come from the census artefact. "
            "ClankOps is additional fleet state and is not part of the VERIFIED denominator."
        ),
        "verified_registered_slugs": [h["slug"] for h in verified_hits],
    }


def _session_rows(store: Store, *, open_only: bool = True) -> list[dict[str, Any]]:
    sql = """
        SELECT s.*, c.slug AS clank_slug, c.display_name AS clank_name,
               m.display_id AS mission_display, m.state AS mission_state, m.objective AS mission_objective
        FROM sessions s
        LEFT JOIN clanks c ON c.clank_id = s.clank_id
        LEFT JOIN missions m ON m.mission_id = s.mission_id
    """
    if open_only:
        sql += " WHERE s.ended_utc IS NULL"
    sql += " ORDER BY s.started_utc, s.session_id"
    return [dict(r) for r in store.conn.execute(sql)]


def _last_event(store: Store, clank_id: str | None) -> dict[str, Any] | None:
    if not clank_id:
        return None
    row = store.conn.execute(
        """
        SELECT ledger_seq, ts_utc, event_type FROM events
        WHERE clank_id = ?
        ORDER BY ledger_seq DESC
        LIMIT 1
        """,
        (clank_id,),
    ).fetchone()
    return dict(row) if row else None


def observe_session(
    store: Store,
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE,
) -> dict[str, Any]:
    instant = _now(store, now)
    started = parse_utc(row.get("started_utc"))
    age = (instant - started) if started else None
    checkpoint = None
    if row.get("mission_id") and row.get("clank_id"):
        checkpoint = store.latest_checkpoint(row["clank_id"], row["mission_id"])
    last_event = _last_event(store, row.get("clank_id"))
    mission_state = row.get("mission_state")
    anomaly = bool(row.get("ended_utc") is None and mission_state and mission_state != MissionState.ACTIVE)
    stale = bool(row.get("ended_utc") is None and age is not None and age >= stale_after)
    process = session_process_view(store, row, now=instant)
    return {
        "session_id": row["session_id"],
        "actor": row.get("actor"),
        "launcher": row.get("launcher"),
        "context_fingerprint": row.get("context_fingerprint"),
        "source": row.get("source"),
        "clank_id": row.get("clank_id"),
        "clank_slug": row.get("clank_slug"),
        "clank_name": row.get("clank_name"),
        "mission_id": row.get("mission_id"),
        "mission_display": row.get("mission_display"),
        "mission_state": mission_state,
        "mission_objective": row.get("mission_objective"),
        "started_utc": row.get("started_utc"),
        "ended_utc": row.get("ended_utc"),
        "open": row.get("ended_utc") is None,
        "stale": stale,
        "age": format_age(age),
        "age_seconds": int(age.total_seconds()) if age is not None else None,
        "latest_checkpoint_utc": checkpoint["recorded_utc"] if checkpoint else None,
        "branch": checkpoint.get("branch") if checkpoint else None,
        "head": checkpoint.get("head") if checkpoint else None,
        "head_short": short_head(checkpoint.get("head") if checkpoint else None),
        "working_tree": checkpoint.get("working_tree") if checkpoint else None,
        "next_action": checkpoint.get("next_action") if checkpoint else None,
        "last_event_utc": last_event["ts_utc"] if last_event else None,
        "anomaly_open_on_non_active_mission": anomaly,
        "anomaly": (
            "open Session attached to a non-ACTIVE Mission" if anomaly else None
        ),
        "managed_process": process,
        "session": process.get("session") or ("OPEN" if row.get("ended_utc") is None else "CLOSED"),
        "handoff": process.get("handoff") or "UNKNOWN",
    }


def open_sessions(
    store: Store, *, now: datetime | None = None, stale_after: timedelta = DEFAULT_STALE
) -> list[dict[str, Any]]:
    return [
        observe_session(store, row, now=now, stale_after=stale_after)
        for row in _session_rows(store, open_only=True)
    ]


def stale_sessions(
    store: Store,
    *,
    older_than: timedelta,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    instant = _now(store, now)
    rows = []
    for row in _session_rows(store, open_only=True):
        observed = observe_session(store, row, now=instant, stale_after=older_than)
        if observed["stale"]:
            rows.append(observed)
    return rows


def list_sessions(
    store: Store,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    open_only: bool = False,
) -> list[dict[str, Any]]:
    """Open and/or closed Sessions with process/handoff views. Read-only."""
    return [
        observe_session(store, row, now=now, stale_after=stale_after)
        for row in _session_rows(store, open_only=open_only)
    ]


def session_anomalies(
    store: Store, *, now: datetime | None = None, stale_after: timedelta = DEFAULT_STALE
) -> list[dict[str, Any]]:
    return [row for row in open_sessions(store, now=now, stale_after=stale_after) if row["anomaly"]]


def _open_for_mission(open_rows: list[dict[str, Any]], mission_id: str | None) -> list[dict[str, Any]]:
    if not mission_id:
        return []
    return [row for row in open_rows if row.get("mission_id") == mission_id]


def fleet_home(
    store: Store,
    *,
    census: dict[str, Any] | None = None,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    include_github: bool = False,
    inspect_local=None,
    inspect_remote=None,
    threshold_label: str | None = None,
    threshold_source: str | None = None,
    live_local: bool | None = None,
) -> dict[str, Any]:
    instant = _now(store, now)
    open_rows = open_sessions(store, now=instant, stale_after=stale_after)
    stale_rows = [row for row in open_rows if row["stale"]]
    missions = [dict(r) for r in store.conn.execute("SELECT state FROM missions")]
    state_counts = {
        "ACTIVE": 0,
        "PAUSED": 0,
        "BLOCKED": 0,
        "PLANNED": 0,
        "COMPLETED": 0,
        "ABANDONED": 0,
        "SUPERSEDED": 0,
    }
    for row in missions:
        state = row["state"]
        state_counts[state] = state_counts.get(state, 0) + 1
    coverage = coverage_report(store, census) if census else None
    table = []
    for clank in store.list_clanks():
        mission = store.active_or_unfinished_mission(clank["clank_id"])
        checkpoint = None
        if mission:
            checkpoint = store.latest_checkpoint(clank["clank_id"], mission["mission_id"])
        sessions = _open_for_mission(open_rows, mission["mission_id"] if mission else None)
        last_event = _last_event(store, clank["clank_id"])
        last_ts = parse_utc(last_event["ts_utc"]) if last_event else None
        since = (instant - last_ts) if last_ts else None
        state = mission["state"] if mission else None
        rec = reconcile_clank(
            store,
            clank["slug"],
            include_github=include_github,
            inspect_local=inspect_local,
            inspect_remote=inspect_remote,
        )
        table.append(
            {
                "clank_id": clank["clank_id"],
                "slug": clank["slug"],
                "display_name": clank["display_name"],
                "lifecycle": clank["lifecycle"],
                "local_path": store.canonical_local_path(clank["clank_id"]),
                "mission_display": mission["display_id"] if mission else None,
                "mission_id": mission["mission_id"] if mission else None,
                "mission_state": state,
                "mission_objective": mission["objective"] if mission else None,
                "actor": sessions[0]["actor"] if sessions else None,
                "open_session_id": sessions[0]["session_id"] if sessions else None,
                "open_session_count": len(sessions),
                "stale_session": any(s["stale"] for s in sessions),
                "anomaly": any(s["anomaly"] for s in sessions),
                "latest_checkpoint_utc": checkpoint["recorded_utc"] if checkpoint else None,
                "branch": checkpoint.get("branch") if checkpoint else None,
                "head": checkpoint.get("head") if checkpoint else None,
                "head_short": short_head(checkpoint.get("head") if checkpoint else None),
                "working_tree": checkpoint.get("working_tree") if checkpoint else None,
                "next_action": checkpoint.get("next_action") if checkpoint else None,
                "age_since_last_event": format_age(since),
                "last_event_utc": last_event["ts_utc"] if last_event else None,
                "reconcile_status": rec["status"],
                "drift": rec["drift"],
                "observed_branch": rec["observed_local"].get("branch"),
                "observed_head_short": rec["head_short_local"],
                "observed_working_tree": rec["observed_local"].get("working_tree"),
            }
        )
    table.sort(
        key=lambda row: (
            MISSION_SORT.get(row["mission_state"] or "", 9),
            0 if row["open_session_id"] else 1,
            row["slug"],
        )
    )
    from clankops.attention import (
        DEFAULT_THRESHOLD_LABEL,
        DEFAULT_THRESHOLD_SOURCE,
        OPERATOR_THRESHOLD_SOURCE,
        attention_report,
    )

    label = threshold_label or (
        DEFAULT_THRESHOLD_LABEL if stale_after == DEFAULT_STALE else str(stale_after)
    )
    source = threshold_source or (
        DEFAULT_THRESHOLD_SOURCE if stale_after == DEFAULT_STALE else OPERATOR_THRESHOLD_SOURCE
    )
    attention = attention_report(
        store,
        now=instant,
        stale_after=stale_after,
        threshold_label=label,
        threshold_source=source,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
        live_local=live_local,
    )
    summary = {
        "registered_clanks": len(table),
        "active_missions": state_counts["ACTIVE"],
        "paused_missions": state_counts["PAUSED"],
        "blocked_missions": state_counts["BLOCKED"],
        "open_sessions": len(open_rows),
        "stale_sessions": len(stale_rows),
        "planned_missions": state_counts["PLANNED"],
        "stale_after": "24h" if stale_after == DEFAULT_STALE else str(stale_after),
        "verified_candidates": coverage["verified_candidates"] if coverage else None,
        "verified_registered": coverage["verified_registered"] if coverage else None,
        "verified_unresolved_count": coverage["verified_unresolved_count"] if coverage else None,
        "anomalies": len(session_anomalies(store, now=instant, stale_after=stale_after)),
        "git_drift": sum(1 for row in table if row.get("reconcile_status") == "drift"),
        "git_aligned": sum(1 for row in table if row.get("reconcile_status") == "aligned"),
        "git_partial": sum(1 for row in table if row.get("reconcile_status") == "partial"),
    }
    return {
        "summary": summary,
        "coverage": coverage,
        "rows": table,
        "open_sessions": open_rows,
        "attention": attention,
    }


def event_summary(event: Any) -> str:
    payload = event.payload or {}
    event_type = str(getattr(event, "event_type", ""))
    if event_type == EventType.MISSION_STATE_RECONCILED:
        from clankops.reconciliation import format_evidence_labels

        from_state = payload.get("from_state") or "unknown"
        to_state = payload.get("to_state") or "unknown"
        reason = payload.get("reason") or "unknown"
        evidence = format_evidence_labels(payload.get("evidence") or [])
        occurred = payload.get("evidence_occurred_at")
        bits = [
            f"RECONCILED {from_state} -> {to_state}",
            f"reason: {reason}",
        ]
        if evidence:
            bits.append(f"evidence: {evidence}")
        bits.append(f"reconciled: {getattr(event, 'ts_utc', None) or payload.get('observed_at') or 'unknown'}")
        if occurred:
            bits.append(f"evidence occurred: {occurred}")
        return " | ".join(bits)
    if event_type == EventType.HANDOFF_RECORDED:
        return f"HANDOFF RECORDED -> {payload.get('to_state') or 'unknown'}"
    if event_type == EventType.LOCAL_GIT_STATE_OBSERVED:
        branch = payload.get("branch") or ("HEAD" if payload.get("detached") else "unknown")
        head = short_head(payload.get("head")) or "unknown"
        dirty = payload.get("dirty")
        tree = "DIRTY" if dirty else ("CLEAN" if dirty is False else "UNKNOWN")
        return f"{branch} {head} {tree}"
    if event_type == EventType.LOCAL_GIT_HARVEST_COMPLETED:
        return (
            f"targets={payload.get('target_count') or 0} "
            f"changed={payload.get('observed_changed') or 0} "
            f"unchanged={payload.get('observed_unchanged') or 0} "
            f"skipped={payload.get('skipped') or 0} "
            f"errors={payload.get('errors') or 0}"
        )
    if event_type == EventType.DEPLOYMENT_OBSERVED:
        env = payload.get("environment") or "unknown"
        surface = payload.get("surface_id") or "unknown"
        host = payload.get("host_identity") or "unknown"
        sha = short_head(payload.get("deployed_sha")) or "unknown"
        note = payload.get("notification_authority") or "unknown"
        return f"{surface} {env} {host} sha={sha} notification={note}"
    for key in (
        "objective",
        "to_state",
        "title",
        "statement",
        "name",
        "slug",
        "completed",
        "current_work",
        "next_action",
        "reason",
    ):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def provenance_labels(event: Any) -> list[str]:
    labels: list[str] = []
    source = getattr(event, "source", None)
    if source:
        labels.append(str(source))
    payload = getattr(event, "payload", None) or {}
    evidence = payload.get("git_evidence") if isinstance(payload, dict) else None
    extra = evidence.get("source") if isinstance(evidence, dict) else None
    if extra and str(extra) not in labels:
        labels.append(str(extra))
    return labels


def _clamp_timeline_limit(limit: int | None) -> int:
    if limit is None:
        return TIMELINE_DEFAULT
    return max(1, min(int(limit), TIMELINE_MAX))


def resolve_timeline_filters(
    store: Store,
    clank_id: str,
    *,
    source: str | None = None,
    event_type: str | None = None,
    mission: str | None = None,
) -> dict[str, str | None]:
    """Validate timeline filters. Values are applied as bound SQL parameters."""
    source_value = None
    if source not in (None, ""):
        try:
            source_value = EventSource(source).value
        except ValueError as exc:
            raise ValidationError(f"unknown timeline source: {source}") from exc
    event_value = None
    if event_type not in (None, ""):
        try:
            event_value = EventType(event_type).value
        except ValueError as exc:
            raise ValidationError(f"unknown timeline event_type: {event_type}") from exc
    mission_id = None
    if mission not in (None, ""):
        row = store.resolve_mission(mission)
        if row["clank_id"] != clank_id:
            raise ValidationError(
                f"mission {row['display_id']} does not belong to this Clank"
            )
        mission_id = row["mission_id"]
    return {"source": source_value, "event_type": event_value, "mission_id": mission_id}


def _timeline_item(event: Any, mission_display: str | None) -> dict[str, Any]:
    return {
        "ledger_seq": event.ledger_seq,
        "ts_utc": event.ts_utc,
        "event_type": event.event_type,
        "actor": event.actor,
        "source": event.source,
        "provenance": provenance_labels(event),
        "session_id": event.session_id,
        "mission_id": event.mission_id,
        "mission_display": mission_display,
        "summary": event_summary(event),
    }


def _mission_display_map(store: Store, events: list[Any]) -> dict[str, str]:
    ids = {getattr(event, "mission_id", None) for event in events}
    ids.discard(None)
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    mapping: dict[str, str] = {}
    for row in store.conn.execute(
        f"SELECT mission_id, display_id FROM missions WHERE mission_id IN ({placeholders})",
        tuple(ids),
    ):
        mapping[row["mission_id"]] = row["display_id"]
    return mapping


def clank_timeline(
    store: Store,
    clank_id: str,
    *,
    limit: int = TIMELINE_DEFAULT,
    source: str | None = None,
    event_type: str | None = None,
    mission: str | None = None,
) -> dict[str, Any]:
    """Newest N matching events, selected at SQL. Returned in ledger_seq order."""
    filters = resolve_timeline_filters(
        store,
        clank_id,
        source=source,
        event_type=event_type,
        mission=mission,
    )
    bounded = _clamp_timeline_limit(limit)
    clauses = ["clank_id = ?"]
    params: list[Any] = [clank_id]
    if filters["mission_id"]:
        clauses.append("mission_id = ?")
        params.append(filters["mission_id"])
    if filters["source"]:
        clauses.append("source = ?")
        params.append(filters["source"])
    if filters["event_type"]:
        clauses.append("event_type = ?")
        params.append(filters["event_type"])
    matched = int(
        store.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE " + " AND ".join(clauses),
            params,
        ).fetchone()["n"]
    )
    events = list_recent_events(
        store.conn,
        clank_id=clank_id,
        mission_id=filters["mission_id"],
        source=filters["source"],
        event_type=filters["event_type"],
        limit=bounded,
    )
    displays = _mission_display_map(store, events)
    rows = [_timeline_item(event, displays.get(event.mission_id)) for event in events]
    return {
        "events": rows,
        "limit": bounded,
        "matched": matched,
        "filters": filters,
    }


def dossier(
    store: Store,
    clank: str,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
    timeline_limit: int | None = None,
    timeline_source: str | None = None,
    timeline_event_type: str | None = None,
    timeline_mission: str | None = None,
) -> dict[str, Any]:
    detail = store.clank_detail(clank)
    brief = store.brief(clank)
    instant = _now(store, now)
    open_rows = [
        row
        for row in open_sessions(store, now=instant, stale_after=stale_after)
        if row.get("clank_id") == detail["clank_id"]
    ]
    mission = brief.get("mission")
    checkpoint = brief.get("checkpoint")
    tasks = [
        dict(r)
        for r in store.conn.execute(
            "SELECT * FROM tasks WHERE clank_id = ? ORDER BY created_utc, task_id",
            (detail["clank_id"],),
        )
    ]
    decisions = [
        dict(r)
        for r in store.conn.execute(
            "SELECT * FROM decisions WHERE clank_id = ? ORDER BY created_utc, decision_id",
            (detail["clank_id"],),
        )
    ]
    timeline = []
    timeline_matched = None
    bounded = any(
        value not in (None, "")
        for value in (timeline_limit, timeline_source, timeline_event_type, timeline_mission)
    )
    if bounded:
        result = clank_timeline(
            store,
            detail["clank_id"],
            limit=TIMELINE_DEFAULT if timeline_limit is None else timeline_limit,
            source=timeline_source,
            event_type=timeline_event_type,
            mission=timeline_mission,
        )
        timeline = result["events"]
        timeline_matched = result["matched"]
    else:
        events = list_events(store.conn, clank_id=detail["clank_id"])
        displays = _mission_display_map(store, events)
        for event in events:
            timeline.append(_timeline_item(event, displays.get(event.mission_id)))
        timeline_matched = len(timeline)
    now_block = {
        "mission_display": mission["display_id"] if mission else None,
        "mission_id": mission["mission_id"] if mission else None,
        "mission_state": mission["state"] if mission else None,
        "mission_objective": mission["objective"] if mission else None,
        "open_sessions": open_rows,
        "checkpoint": checkpoint,
        "branch": checkpoint.get("branch") if checkpoint else None,
        "head": checkpoint.get("head") if checkpoint else None,
        "working_tree": checkpoint.get("working_tree") if checkpoint else None,
        "tests": checkpoint.get("tests") if checkpoint else None,
        "blockers": brief.get("blockers") or [],
        "outstanding_tasks": brief.get("outstanding_tasks") or [],
        "next_action": brief.get("next_action"),
    }
    rec = reconcile_clank(
        store,
        detail["slug"],
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    from clankops.deployment import current_deployments
    from clankops.harvest import local_git_harvest_view
    from clankops.reconciliation import reconciliations_for_clank

    recs = reconciliations_for_clank(store, detail["clank_id"])
    rec_by_mission = {row["mission_id"]: row for row in recs}
    missions = []
    for mission in detail.get("missions") or []:
        row = dict(mission)
        row["reconciliation"] = rec_by_mission.get(mission["mission_id"])
        missions.append(row)

    return {
        "identity": brief["identity"],
        "lifecycle_state": brief.get("lifecycle_state"),
        "now": now_block,
        "reconcile": rec,
        "timeline": timeline,
        "timeline_matched": timeline_matched,
        "missions": missions,
        "reconciliations": recs,
        "features": detail.get("features") or [],
        "tasks": tasks,
        "decisions": decisions,
        "artifacts": [
            dict(r)
            for r in store.conn.execute(
                "SELECT * FROM artifacts WHERE clank_id = ? ORDER BY created_utc, artifact_id",
                (detail["clank_id"],),
            )
        ],
        "deployments": current_deployments(store, detail["clank_id"]),
        "local_git_harvest": local_git_harvest_view(store, detail["clank_id"], now=instant),
        "brief": brief,
    }


def ledger_fingerprint(store: Store) -> dict[str, Any]:
    row = store.conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(ledger_seq), 0) AS max_seq FROM events"
    ).fetchone()
    return {"event_count": int(row["n"]), "max_ledger_seq": int(row["max_seq"])}
