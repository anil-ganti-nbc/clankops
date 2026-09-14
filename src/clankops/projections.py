"""Projection application and rebuild.

Current state is a function of the event log. Replay uses canonical
`ledger_seq` order. Mission display-id allocation is reseeded from
MISSION_CREATED events so a fresh database cannot reissue COPS-000001.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from clankops.enums import EventType
from clankops.events import Event, LEDGER_ORDER_SQL, event_from_row
from clankops.ids import parse_mission_display_n
from clankops.schema import PROJECTION_TABLES

Handler = Callable[[sqlite3.Connection, Event], None]


def _payload(event: Event) -> dict[str, Any]:
    return event.payload


def _apply_clank_registered(conn: sqlite3.Connection, event: Event) -> None:
    p = _payload(event)
    conn.execute(
        """
        INSERT INTO clanks (
            clank_id, slug, display_name, description, lifecycle,
            first_known_utc, created_event_id, classification, provenance_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.clank_id,
            p["slug"],
            p.get("display_name") or p["slug"],
            p.get("description"),
            p.get("lifecycle") or "UNKNOWN",
            p.get("first_known_utc"),
            event.event_id,
            p.get("classification"),
            event.source,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clank_aliases(alias, clank_id) VALUES (?, ?)",
        (p["slug"], event.clank_id),
    )
    for alias in p.get("aliases") or []:
        if alias:
            conn.execute(
                "INSERT OR IGNORE INTO clank_aliases(alias, clank_id) VALUES (?, ?)",
                (str(alias), event.clank_id),
            )
    for ref in p.get("refs") or []:
        conn.execute(
            """
            INSERT OR IGNORE INTO clank_refs(clank_id, ref_kind, ref_value, is_canonical)
            VALUES (?, ?, ?, ?)
            """,
            (
                event.clank_id,
                ref["kind"],
                ref["value"],
                1 if ref.get("canonical") else 0,
            ),
        )


def _apply_clank_alias_added(conn: sqlite3.Connection, event: Event) -> None:
    alias = event.payload["alias"]
    conn.execute(
        "INSERT OR IGNORE INTO clank_aliases(alias, clank_id) VALUES (?, ?)",
        (alias, event.clank_id),
    )


def _apply_clank_ref_updated(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    if p.get("replace_kind"):
        conn.execute(
            "DELETE FROM clank_refs WHERE clank_id = ? AND ref_kind = ?",
            (event.clank_id, p["kind"]),
        )
    conn.execute(
        """
        INSERT OR REPLACE INTO clank_refs(clank_id, ref_kind, ref_value, is_canonical)
        VALUES (?, ?, ?, ?)
        """,
        (
            event.clank_id,
            p["kind"],
            p["value"],
            1 if p.get("canonical") else 0,
        ),
    )


def _apply_clank_lifecycle_changed(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        "UPDATE clanks SET lifecycle = ? WHERE clank_id = ?",
        (event.payload["lifecycle"], event.clank_id),
    )


def _apply_mission_created(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO missions (
            mission_id, display_id, clank_id, objective, state,
            created_utc, updated_utc, superseded_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.mission_id,
            p["display_id"],
            event.clank_id,
            p["objective"],
            p.get("state") or "ACTIVE",
            event.ts_utc,
            event.ts_utc,
            None,
        ),
    )


def _apply_mission_state_changed(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        UPDATE missions
        SET state = ?, updated_utc = ?, superseded_by = COALESCE(?, superseded_by)
        WHERE mission_id = ?
        """,
        (p["to_state"], event.ts_utc, p.get("superseded_by"), event.mission_id),
    )


def _apply_mission_state_reconciled(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    evidence = p.get("evidence") or []
    conn.execute(
        """
        INSERT INTO mission_reconciliations (
            reconciliation_id, mission_id, clank_id, from_state, to_state,
            reason, evidence_json, evidence_occurred_at, reconciliation_basis,
            actor, source, observed_at, ledger_seq, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["reconciliation_id"],
            event.mission_id or p.get("mission_id"),
            event.clank_id or p.get("clank_id"),
            p["from_state"],
            p["to_state"],
            p["reason"],
            json.dumps(evidence, sort_keys=True, default=str),
            p.get("evidence_occurred_at"),
            p.get("reconciliation_basis"),
            event.actor,
            event.source,
            p.get("observed_at") or event.ts_utc,
            event.ledger_seq,
            event.ts_utc,
        ),
    )
    conn.execute(
        """
        UPDATE missions
        SET state = ?, updated_utc = ?
        WHERE mission_id = ?
        """,
        (p["to_state"], event.ts_utc, event.mission_id or p.get("mission_id")),
    )


def _apply_handoff_recorded(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO handoffs (
            handoff_id, session_id, mission_id, clank_id, to_state,
            checkpoint_id, actor, source, observed_at, ledger_seq, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["handoff_id"],
            event.session_id or p.get("session_id"),
            event.mission_id or p.get("mission_id"),
            event.clank_id or p.get("clank_id"),
            p["to_state"],
            p.get("checkpoint_id"),
            event.actor,
            event.source,
            p.get("observed_at") or event.ts_utc,
            event.ledger_seq,
            event.ts_utc,
        ),
    )


def _apply_session_started(conn: sqlite3.Connection, event: Event) -> None:
    payload = event.payload or {}
    provenance = event.provenance or {}
    launcher = payload.get("launcher") or provenance.get("launcher")
    fingerprint = payload.get("context_fingerprint") or provenance.get("context_fingerprint")
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, clank_id, mission_id, actor, started_utc, ended_utc,
            launcher, context_fingerprint, source
        )
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            event.session_id,
            event.clank_id,
            event.mission_id,
            event.actor,
            event.ts_utc,
            (str(launcher).strip() or None) if launcher else None,
            (str(fingerprint).strip() or None) if fingerprint else None,
            event.source,
        ),
    )


def _apply_session_ended(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        "UPDATE sessions SET ended_utc = ? WHERE session_id = ?",
        (event.ts_utc, event.session_id),
    )


def _apply_checkpoint(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    evidence = p.get("git_evidence") or {}
    conn.execute(
        """
        INSERT INTO checkpoints (
            checkpoint_id, mission_id, clank_id, event_id, recorded_utc,
            completed, current_work, next_action, outstanding_json, blockers_json,
            tests, branch, head, working_tree, artifacts_json, notes, session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p.get("checkpoint_id") or event.event_id,
            event.mission_id,
            event.clank_id,
            event.event_id,
            event.ts_utc,
            p.get("completed"),
            p.get("current_work"),
            p.get("next_action"),
            json.dumps(p.get("outstanding") or [], sort_keys=True),
            json.dumps(p.get("blockers") or [], sort_keys=True),
            p.get("tests"),
            evidence.get("branch") or p.get("branch"),
            evidence.get("head") or p.get("head"),
            evidence.get("working_tree") or p.get("working_tree"),
            json.dumps(p.get("artifacts") or [], sort_keys=True),
            p.get("notes"),
            event.session_id,
        ),
    )


def _apply_task_created(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO tasks (
            task_id, mission_id, clank_id, title, state, created_utc, updated_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["task_id"],
            event.mission_id,
            event.clank_id,
            p["title"],
            p.get("state") or "TODO",
            event.ts_utc,
            event.ts_utc,
        ),
    )


def _apply_task_state_changed(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        "UPDATE tasks SET state = ?, updated_utc = ? WHERE task_id = ?",
        (event.payload["to_state"], event.ts_utc, event.payload["task_id"]),
    )


def _apply_feature_added(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO features (
            feature_id, clank_id, name, state, created_utc, updated_utc
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            p["feature_id"],
            event.clank_id,
            p["name"],
            p.get("state") or "PRESENT",
            event.ts_utc,
            event.ts_utc,
        ),
    )


def _apply_feature_state_changed(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        "UPDATE features SET state = ?, updated_utc = ? WHERE feature_id = ?",
        (event.payload["to_state"], event.ts_utc, event.payload["feature_id"]),
    )


def _apply_decision_recorded(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO decisions (
            decision_id, clank_id, mission_id, statement, rationale, alternatives,
            actor, created_utc, superseded_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            p["decision_id"],
            event.clank_id,
            event.mission_id,
            p["statement"],
            p.get("rationale") or p.get("why"),
            p.get("alternatives"),
            event.actor,
            event.ts_utc,
        ),
    )


def _apply_decision_superseded(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        "UPDATE decisions SET superseded_by = ? WHERE decision_id = ?",
        (event.payload["superseded_by"], event.payload["decision_id"]),
    )


def _apply_blocker_added(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO blockers (
            blocker_id, mission_id, clank_id, description, state, created_utc,
            resolved_utc, resolution
        ) VALUES (?, ?, ?, ?, 'OPEN', ?, NULL, NULL)
        """,
        (
            p["blocker_id"],
            event.mission_id,
            event.clank_id,
            p["description"],
            event.ts_utc,
        ),
    )


def _apply_blocker_resolved(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        """
        UPDATE blockers
        SET state = 'RESOLVED', resolved_utc = ?, resolution = ?
        WHERE blocker_id = ?
        """,
        (event.ts_utc, event.payload.get("resolution"), event.payload["blocker_id"]),
    )


def _apply_artifact_attached(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO artifacts (
            artifact_id, clank_id, mission_id, session_id, kind, ref, title,
            source, created_utc, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["artifact_id"],
            event.clank_id,
            event.mission_id,
            event.session_id,
            p["kind"],
            p["ref"],
            p.get("title"),
            p.get("artifact_source") or event.source,
            event.ts_utc,
            json.dumps(p.get("metadata") or {}, sort_keys=True),
        ),
    )


def _apply_relationship(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO relationships (
            relationship_id, from_clank_id, to_clank_id, kind, created_utc, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            p["relationship_id"],
            p["from_clank_id"],
            p["to_clank_id"],
            p["kind"],
            event.ts_utc,
            json.dumps(p.get("metadata") or {}, sort_keys=True),
        ),
    )


def _apply_census_candidate(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT OR REPLACE INTO census_candidates (
            candidate_id, slug, local_path, remote, classification, clank_id,
            payload_json, imported_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["candidate_id"],
            p.get("slug") or p.get("name"),
            p.get("local_path"),
            p.get("remote"),
            p["classification"],
            p.get("clank_id") or event.clank_id,
            json.dumps(p, sort_keys=True, default=str),
            event.ts_utc,
        ),
    )


def _apply_deployment_observed(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO deployment_observations (
            observation_id, clank_id, mission_id, surface_id, environment,
            host_identity, runtime_path, deployed_sha, image_id, runtime_identity,
            deployed, running, scheduler, scheduler_cadence, state_store,
            collection_authority, notification_authority, webhook_configured,
            sent_count, observed_at, observed_how, observer, source, notes,
            metadata_json, created_utc, ledger_seq
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["observation_id"],
            event.clank_id,
            event.mission_id,
            p["surface_id"],
            p["environment"],
            p["host_identity"],
            p.get("runtime_path"),
            p.get("deployed_sha"),
            p.get("image_id"),
            p.get("runtime_identity"),
            p.get("deployed") or "unknown",
            p.get("running") or "unknown",
            p.get("scheduler") or "unknown",
            p.get("scheduler_cadence"),
            p.get("state_store"),
            p.get("collection_authority") or "unknown",
            p.get("notification_authority") or "unknown",
            p.get("webhook_configured") or "unknown",
            p.get("sent_count"),
            p.get("observed_at") or event.ts_utc,
            p["observed_how"],
            p.get("observer") or event.actor,
            p.get("source") or event.source,
            p.get("notes"),
            json.dumps(p.get("metadata") or {}, sort_keys=True),
            event.ts_utc,
            event.ledger_seq,
        ),
    )


def _apply_agent_process_observation(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO agent_process_observations (
            observation_id, session_id, mission_id, clank_id, kind,
            actor, launcher, context_fingerprint, executable, argv_count,
            argv_redacted, exit_code, error, observed_at, observed_how,
            observer, source, ledger_seq, created_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["observation_id"],
            p.get("session_id") or event.session_id,
            p.get("mission_id") or event.mission_id,
            p.get("clank_id") or event.clank_id,
            p["kind"],
            p.get("actor") or event.actor,
            p.get("launcher"),
            p.get("context_fingerprint"),
            p.get("executable"),
            p.get("argv_count"),
            1 if p.get("argv_redacted") else 0,
            p.get("exit_code"),
            p.get("error"),
            p.get("observed_at") or event.ts_utc,
            p.get("observed_how"),
            p.get("observer") or event.actor,
            p.get("source") or event.source,
            event.ledger_seq,
            event.ts_utc,
        ),
    )


def _semantic_state_json(payload: dict[str, Any]) -> str:
    skip = {"observation_id", "observed_at"}
    state = {key: value for key, value in payload.items() if key not in skip}
    return json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)


def _apply_local_git_state_observed(conn: sqlite3.Connection, event: Event) -> None:
    p = _payload(event)
    conn.execute(
        """
        INSERT INTO local_git_observations (
            observation_id, clank_id, checkout_key, checkout_path,
            state_fingerprint, state_json, event_id, ledger_seq, observed_at, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["observation_id"],
            p.get("clank_id") or event.clank_id,
            p["checkout_key"],
            p["checkout_path"],
            p["state_fingerprint"],
            _semantic_state_json(p),
            event.event_id,
            event.ledger_seq,
            p.get("observed_at") or event.ts_utc,
            p.get("source") or event.source,
        ),
    )


def _apply_local_git_harvest_completed(conn: sqlite3.Connection, event: Event) -> None:
    p = _payload(event)
    conn.execute(
        """
        INSERT INTO local_git_harvest_runs (
            run_id, scope, target_slug, started_at, finished_at, target_count,
            observed_changed, observed_unchanged, unavailable, skipped, errors,
            event_id, ledger_seq, actor, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["run_id"],
            p.get("scope") or "fleet",
            p.get("target"),
            p.get("started_at") or event.ts_utc,
            p.get("finished_at") or event.ts_utc,
            int(p.get("target_count") or 0),
            int(p.get("observed_changed") or 0),
            int(p.get("observed_unchanged") or 0),
            int(p.get("unavailable") or 0),
            int(p.get("skipped") or 0),
            int(p.get("errors") or 0),
            event.event_id,
            event.ledger_seq,
            event.actor,
            p.get("source") or event.source,
        ),
    )
    for row in p.get("results") or []:
        detached = row.get("detached")
        dirty = row.get("dirty")
        conn.execute(
            """
            INSERT INTO local_git_harvest_results (
                result_id, run_id, clank_id, clank_slug, checkout_key, checkout_path,
                result_code, observation_event_id, observation_ledger_seq,
                state_fingerprint, error_class, detail, branch, detached, head,
                dirty, dirty_count, event_id, ledger_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("result_id") or event.event_id,
                p["run_id"],
                row["clank_id"],
                row.get("clank_slug"),
                row.get("checkout_key"),
                row.get("checkout_path"),
                row.get("result_code") or row.get("result"),
                row.get("observation_event_id"),
                row.get("observation_ledger_seq"),
                row.get("state_fingerprint"),
                row.get("error_class"),
                row.get("detail"),
                row.get("branch"),
                None if detached is None else (1 if detached else 0),
                row.get("head"),
                None if dirty is None else (1 if dirty else 0),
                row.get("dirty_count"),
                event.event_id,
                event.ledger_seq,
            ),
        )


HANDLERS: dict[str, Handler] = {
    EventType.CLANK_REGISTERED: _apply_clank_registered,
    EventType.CLANK_ALIAS_ADDED: _apply_clank_alias_added,
    EventType.CLANK_REF_UPDATED: _apply_clank_ref_updated,
    EventType.CLANK_LIFECYCLE_CHANGED: _apply_clank_lifecycle_changed,
    EventType.MISSION_CREATED: _apply_mission_created,
    EventType.MISSION_STATE_CHANGED: _apply_mission_state_changed,
    EventType.MISSION_STATE_RECONCILED: _apply_mission_state_reconciled,
    EventType.HANDOFF_RECORDED: _apply_handoff_recorded,
    EventType.SESSION_STARTED: _apply_session_started,
    EventType.SESSION_ENDED: _apply_session_ended,
    EventType.CHECKPOINT_RECORDED: _apply_checkpoint,
    EventType.TASK_CREATED: _apply_task_created,
    EventType.TASK_STATE_CHANGED: _apply_task_state_changed,
    EventType.FEATURE_ADDED: _apply_feature_added,
    EventType.FEATURE_STATE_CHANGED: _apply_feature_state_changed,
    EventType.DECISION_RECORDED: _apply_decision_recorded,
    EventType.DECISION_SUPERSEDED: _apply_decision_superseded,
    EventType.BLOCKER_ADDED: _apply_blocker_added,
    EventType.BLOCKER_RESOLVED: _apply_blocker_resolved,
    EventType.ARTIFACT_ATTACHED: _apply_artifact_attached,
    EventType.RELATIONSHIP_RECORDED: _apply_relationship,
    EventType.CENSUS_CANDIDATE_RECORDED: _apply_census_candidate,
    EventType.DEPLOYMENT_OBSERVED: _apply_deployment_observed,
    EventType.AGENT_PROCESS_EXITED: _apply_agent_process_observation,
    EventType.AGENT_PROCESS_START_FAILED: _apply_agent_process_observation,
    EventType.LOCAL_GIT_STATE_OBSERVED: _apply_local_git_state_observed,
    EventType.LOCAL_GIT_HARVEST_COMPLETED: _apply_local_git_harvest_completed,
}


def apply_event(conn: sqlite3.Connection, event: Event) -> None:
    handler = HANDLERS.get(event.event_type)
    if handler is None:
        raise ValueError(f"unknown event type: {event.event_type}")
    handler(conn, event)


def wipe_projections(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in PROJECTION_TABLES:
            conn.execute(f"DELETE FROM {table}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def max_issued_mission_display_n(conn: sqlite3.Connection) -> int:
    """Highest COPS-N issued, derived only from immutable MISSION_CREATED events."""
    highest = 0
    rows = conn.execute(
        "SELECT payload_json FROM events WHERE event_type = ?",
        (EventType.MISSION_CREATED,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row[0])
        parsed = parse_mission_display_n(payload.get("display_id"))
        if parsed is not None and parsed > highest:
            highest = parsed
    return highest


def reseed_mission_display_sequence(conn: sqlite3.Connection) -> int:
    nxt = max_issued_mission_display_n(conn) + 1
    conn.execute(
        """
        INSERT INTO id_sequences(name, next_value) VALUES ('mission_display', ?)
        ON CONFLICT(name) DO UPDATE SET next_value = excluded.next_value
        """,
        (nxt,),
    )
    return nxt


def rebuild_projections(conn: sqlite3.Connection) -> int:
    """Rebuild every projection table from the event log. Returns event count."""
    wipe_projections(conn)
    rows = conn.execute(f"SELECT * FROM events {LEDGER_ORDER_SQL}").fetchall()
    count = 0
    for row in rows:
        apply_event(conn, event_from_row(row))
        count += 1
    reseed_mission_display_sequence(conn)
    conn.commit()
    return count


def dump_projection_state(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Deterministic snapshot of projection tables for rebuild tests."""
    state: dict[str, list[dict[str, Any]]] = {}
    for table in sorted(PROJECTION_TABLES):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        dumped = []
        for row in rows:
            dumped.append({k: row[k] for k in row.keys()})
        dumped.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        state[table] = dumped
    return state
