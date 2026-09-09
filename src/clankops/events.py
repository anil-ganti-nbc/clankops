"""Append-only event records."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from clankops import SCHEMA_VERSION
from clankops.clock import Clock, SystemClock, isoformat_utc
from clankops.enums import EventSource, EventType
from clankops.errors import AppendOnlyViolation
from clankops.ids import new_id


def dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class Event:
    event_id: str
    ts_utc: str
    event_type: str
    actor: str
    source: str
    payload: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    clank_id: str | None = None
    mission_id: str | None = None
    session_id: str | None = None

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.event_id,
            self.ts_utc,
            self.clank_id,
            self.mission_id,
            self.session_id,
            self.event_type,
            self.actor,
            self.source,
            dumps(self.payload),
            dumps(self.provenance),
            self.schema_version,
        )


def event_from_row(row: sqlite3.Row) -> Event:
    return Event(
        event_id=row["event_id"],
        ts_utc=row["ts_utc"],
        clank_id=row["clank_id"],
        mission_id=row["mission_id"],
        session_id=row["session_id"],
        event_type=row["event_type"],
        actor=row["actor"],
        source=row["source"],
        payload=json.loads(row["payload_json"]),
        provenance=json.loads(row["provenance_json"]),
        schema_version=row["schema_version"],
    )


def append_event(
    conn: sqlite3.Connection,
    *,
    event_type: EventType | str,
    actor: str,
    source: EventSource | str,
    payload: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    clank_id: str | None = None,
    mission_id: str | None = None,
    session_id: str | None = None,
    clock: Clock | None = None,
    event_id: str | None = None,
) -> Event:
    clock = clock or SystemClock()
    event = Event(
        event_id=event_id or new_id(),
        ts_utc=isoformat_utc(clock.now()),
        event_type=str(event_type),
        actor=actor,
        source=str(source),
        payload=payload,
        provenance=provenance or {"recorder": "clankops"},
        clank_id=clank_id,
        mission_id=mission_id,
        session_id=session_id,
    )
    try:
        conn.execute(
            """
            INSERT INTO events (
                event_id, ts_utc, clank_id, mission_id, session_id,
                event_type, actor, source, payload_json, provenance_json,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            event.to_row(),
        )
    except sqlite3.IntegrityError as exc:
        raise AppendOnlyViolation(str(exc)) from exc
    return event


def list_events(
    conn: sqlite3.Connection,
    *,
    clank_id: str | None = None,
    mission_id: str | None = None,
    limit: int | None = None,
) -> list[Event]:
    sql = "SELECT * FROM events"
    params: list[Any] = []
    clauses: list[str] = []
    if clank_id:
        clauses.append("clank_id = ?")
        params.append(clank_id)
    if mission_id:
        clauses.append("mission_id = ?")
        params.append(mission_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ts_utc ASC, event_id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [event_from_row(row) for row in conn.execute(sql, params)]


def assert_append_only(conn: sqlite3.Connection) -> None:
    """Raise if UPDATE or DELETE against events is possible."""
    row = conn.execute("SELECT event_id FROM events LIMIT 1").fetchone()
    if row is None:
        return
    try:
        conn.execute(
            "UPDATE events SET actor = actor || '_mutated' WHERE event_id = ?",
            (row[0],),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return
    conn.rollback()
    raise AppendOnlyViolation("events UPDATE was not blocked")
