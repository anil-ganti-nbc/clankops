"""SQLite schema migrations.

Projection tables are rebuildable from `events`. The events table is
append-only at the SQL layer (UPDATE/DELETE triggers abort).
"""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "foundation0_ledger",
        """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    clank_id TEXT,
    mission_id TEXT,
    session_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);

CREATE INDEX idx_events_clank_ts ON events(clank_id, ts_utc, event_id);
CREATE INDEX idx_events_mission_ts ON events(mission_id, ts_utc, event_id);
CREATE INDEX idx_events_type_ts ON events(event_type, ts_utc);

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only');
END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only');
END;

CREATE TABLE id_sequences (
    name TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL
);

INSERT INTO id_sequences(name, next_value) VALUES ('mission_display', 1);

CREATE TABLE clanks (
    clank_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT,
    lifecycle TEXT NOT NULL,
    first_known_utc TEXT,
    created_event_id TEXT NOT NULL,
    classification TEXT,
    provenance_source TEXT NOT NULL
);

CREATE TABLE clank_aliases (
    alias TEXT PRIMARY KEY,
    clank_id TEXT NOT NULL REFERENCES clanks(clank_id)
);

CREATE TABLE clank_refs (
    clank_id TEXT NOT NULL REFERENCES clanks(clank_id),
    ref_kind TEXT NOT NULL,
    ref_value TEXT NOT NULL,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (clank_id, ref_kind, ref_value)
);

CREATE TABLE missions (
    mission_id TEXT PRIMARY KEY,
    display_id TEXT NOT NULL UNIQUE,
    clank_id TEXT NOT NULL REFERENCES clanks(clank_id),
    objective TEXT NOT NULL,
    state TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL,
    superseded_by TEXT
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    clank_id TEXT,
    mission_id TEXT,
    actor TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    ended_utc TEXT
);

CREATE TABLE features (
    feature_id TEXT PRIMARY KEY,
    clank_id TEXT NOT NULL REFERENCES clanks(clank_id),
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
    clank_id TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL
);

CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    clank_id TEXT NOT NULL,
    mission_id TEXT,
    statement TEXT NOT NULL,
    rationale TEXT,
    alternatives TEXT,
    actor TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    superseded_by TEXT
);

CREATE TABLE blockers (
    blocker_id TEXT PRIMARY KEY,
    mission_id TEXT,
    clank_id TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    resolved_utc TEXT,
    resolution TEXT
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    clank_id TEXT,
    mission_id TEXT,
    session_id TEXT,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    title TEXT,
    source TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    metadata_json TEXT
);

CREATE TABLE relationships (
    relationship_id TEXT PRIMARY KEY,
    from_clank_id TEXT NOT NULL,
    to_clank_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    clank_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    recorded_utc TEXT NOT NULL,
    completed TEXT,
    current_work TEXT,
    next_action TEXT,
    outstanding_json TEXT,
    blockers_json TEXT,
    tests TEXT,
    branch TEXT,
    head TEXT,
    working_tree TEXT,
    artifacts_json TEXT,
    notes TEXT
);

CREATE TABLE census_candidates (
    candidate_id TEXT PRIMARY KEY,
    slug TEXT,
    local_path TEXT,
    remote TEXT,
    classification TEXT NOT NULL,
    clank_id TEXT,
    payload_json TEXT NOT NULL,
    imported_utc TEXT NOT NULL
);
""",
    )
]

PROJECTION_TABLES = (
    "census_candidates",
    "checkpoints",
    "relationships",
    "artifacts",
    "blockers",
    "decisions",
    "tasks",
    "features",
    "sessions",
    "missions",
    "clank_refs",
    "clank_aliases",
    "clanks",
)
