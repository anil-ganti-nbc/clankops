"""Projection application and rebuild.

Current state is always a function of the event log. `rebuild_projections`
wipes projection tables and replays every event in (ts_utc, event_id) order.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from clankops.enums import EventType
from clankops.events import Event, event_from_row
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


def _apply_session_started(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        """
        INSERT INTO sessions (session_id, clank_id, mission_id, actor, started_utc, ended_utc)
        VALUES (?, ?, ?, ?, ?, NULL)
        """,
        (event.session_id, event.clank_id, event.mission_id, event.actor, event.ts_utc),
    )


def _apply_session_ended(conn: sqlite3.Connection, event: Event) -> None:
    conn.execute(
        "UPDATE sessions SET ended_utc = ? WHERE session_id = ?",
        (event.ts_utc, event.session_id),
    )


def _apply_checkpoint(conn: sqlite3.Connection, event: Event) -> None:
    p = event.payload
    conn.execute(
        """
        INSERT INTO checkpoints (
            checkpoint_id, mission_id, clank_id, event_id, recorded_utc,
            completed, current_work, next_action, outstanding_json, blockers_json,
            tests, branch, head, working_tree, artifacts_json, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            p.get("branch"),
            p.get("head"),
            p.get("working_tree"),
            json.dumps(p.get("artifacts") or [], sort_keys=True),
            p.get("notes"),
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


HANDLERS: dict[str, Handler] = {
    EventType.CLANK_REGISTERED: _apply_clank_registered,
    EventType.CLANK_ALIAS_ADDED: _apply_clank_alias_added,
    EventType.CLANK_REF_UPDATED: _apply_clank_ref_updated,
    EventType.CLANK_LIFECYCLE_CHANGED: _apply_clank_lifecycle_changed,
    EventType.MISSION_CREATED: _apply_mission_created,
    EventType.MISSION_STATE_CHANGED: _apply_mission_state_changed,
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


def rebuild_projections(conn: sqlite3.Connection) -> int:
    """Rebuild every projection table from the event log. Returns event count."""
    wipe_projections(conn)
    rows = conn.execute(
        "SELECT * FROM events ORDER BY ts_utc ASC, event_id ASC"
    ).fetchall()
    count = 0
    for row in rows:
        apply_event(conn, event_from_row(row))
        count += 1
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
