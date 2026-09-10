"""Append-only event records.

`event_id` is the durable globally unique identity (UUIDv7).
`ledger_seq` is the canonical order within this SQLite ledger. It is
assigned at append time, unique, monotonic, and immutable. Timestamps
remain evidence; they are not the ordering primitive.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from clankops import SCHEMA_VERSION
from clankops.clock import Clock, SystemClock, isoformat_utc
from clankops.enums import EventSource, EventType
from clankops.errors import AppendOnlyViolation
from clankops.ids import new_id

LEDGER_ORDER_SQL = "ORDER BY ledger_seq ASC"

EVENT_COLUMNS = (
    "ledger_seq, event_id, ts_utc, clank_id, mission_id, session_id, "
    "event_type, actor, source, payload_json, provenance_json, schema_version"
)


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
    ledger_seq: int | None = None

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.ledger_seq,
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
    keys = set(row.keys())
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
        ledger_seq=row["ledger_seq"] if "ledger_seq" in keys else None,
    )


def allocate_ledger_seq(conn: sqlite3.Connection) -> int:
    """Monotonic seq assigned by this ledger at append time."""
    row = conn.execute("SELECT next_seq FROM ledger_head WHERE id = 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO ledger_head(id, next_seq) VALUES (1, 2)")
        return 1
    seq = int(row[0])
    conn.execute("UPDATE ledger_head SET next_seq = ? WHERE id = 1", (seq + 1,))
    return seq


def reseed_ledger_head(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(ledger_seq), 0) FROM events").fetchone()
    nxt = int(row[0]) + 1
    conn.execute(
        """
        INSERT INTO ledger_head(id, next_seq) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET next_seq = excluded.next_seq
        """,
        (nxt,),
    )
    return nxt


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
        ledger_seq=allocate_ledger_seq(conn),
    )
    try:
        conn.execute(
            f"""
            INSERT INTO events ({EVENT_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    session_id: str | None = None,
    limit: int | None = None,
) -> list[Event]:
    sql = f"SELECT {EVENT_COLUMNS} FROM events"
    params: list[Any] = []
    clauses: list[str] = []
    if clank_id:
        clauses.append("clank_id = ?")
        params.append(clank_id)
    if mission_id:
        clauses.append("mission_id = ?")
        params.append(mission_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" {LEDGER_ORDER_SQL}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [event_from_row(row) for row in conn.execute(sql, params)]


def copy_events(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    """Copy ONLY the immutable event log into dst, preserving ledger_seq."""
    rows = src.execute(f"SELECT {EVENT_COLUMNS} FROM events {LEDGER_ORDER_SQL}").fetchall()
    dst.executemany(
        f"INSERT INTO events ({EVENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [tuple(row) for row in rows],
    )
    reseed_ledger_head(dst)
    dst.commit()
    return len(rows)


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


def assert_ledger_seq_immutable(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT event_id, ledger_seq FROM events LIMIT 1").fetchone()
    if row is None:
        return
    try:
        conn.execute(
            "UPDATE events SET ledger_seq = ledger_seq + 1 WHERE event_id = ?",
            (row[0],),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return
    conn.rollback()
    raise AppendOnlyViolation("ledger_seq UPDATE was not blocked")
