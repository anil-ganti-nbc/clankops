"""Domain operations. Every mutation appends an event, then updates projections."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from clankops.clock import Clock, SystemClock
from clankops.enums import (
    REGISTERABLE_CLASSIFICATIONS,
    TASK_TRANSITIONS,
    FEATURE_TRANSITIONS,
    MISSION_TRANSITIONS,
    MISSION_WORK_STOPPED,
    BlockerState,
    CensusClassification,
    ClankLifecycle,
    EventSource,
    EventType,
    FeatureState,
    MissionState,
    TaskState,
)
from clankops.errors import DuplicateError, InvalidTransitionError, NotFoundError, ValidationError
from clankops.events import Event, append_event, list_events
from clankops.ids import format_mission_display_id, is_uuid, new_id
from clankops.projections import (
    apply_event,
    dump_projection_state,
    max_issued_mission_display_n,
    rebuild_projections,
)


def _slugify(value: str) -> str:
    cleaned = []
    prev_dash = False
    for ch in value.strip().lower().replace("_", "-").replace(" ", "-"):
        if ch.isalnum():
            cleaned.append(ch)
            prev_dash = False
        elif ch == "-" and not prev_dash:
            cleaned.append("-")
            prev_dash = True
    return "".join(cleaned).strip("-")


@dataclass
class Store:
    conn: sqlite3.Connection
    clock: Clock
    default_actor: str = "user"
    default_source: str = EventSource.USER
    default_session_id: str | None = None

    def commit(self) -> None:
        self.conn.commit()

    def _emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        actor: str | None = None,
        source: str | EventSource | None = None,
        clank_id: str | None = None,
        mission_id: str | None = None,
        session_id: str | None = None,
        provenance: dict[str, Any] | None = None,
        bind_session: bool = True,
    ) -> Event:
        actor = actor or self.default_actor
        if bind_session and session_id is None:
            session_id = self.resolve_active_session(
                mission_id=mission_id,
                actor=actor,
                clank_id=clank_id,
            )
        event = append_event(
            self.conn,
            event_type=event_type,
            actor=actor,
            source=source or self.default_source,
            payload=payload,
            provenance=provenance or {"recorder": "clankops.store"},
            clank_id=clank_id,
            mission_id=mission_id,
            session_id=session_id,
            clock=self.clock,
        )
        apply_event(self.conn, event)
        return event

    # --- lookup ---

    def resolve_clank(self, token: str) -> dict[str, Any]:
        token = token.strip()
        row = None
        if is_uuid(token):
            row = self.conn.execute(
                "SELECT * FROM clanks WHERE clank_id = ?", (token,)
            ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT c.* FROM clanks c WHERE c.slug = ?", (token,)
            ).fetchone()
        if row is None:
            row = self.conn.execute(
                """
                SELECT c.* FROM clanks c
                JOIN clank_aliases a ON a.clank_id = c.clank_id
                WHERE a.alias = ?
                """,
                (token,),
            ).fetchone()
        if row is None and len(token) >= 8:
            matches = self.conn.execute(
                "SELECT * FROM clanks WHERE clank_id LIKE ?", (token + "%",)
            ).fetchall()
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise NotFoundError(f"clank not found: {token}")
        return dict(row)

    def resolve_mission(self, token: str) -> dict[str, Any]:
        token = token.strip()
        row = None
        if is_uuid(token):
            row = self.conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?", (token,)
            ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM missions WHERE display_id = ?", (token.upper(),)
            ).fetchone()
        if row is None and len(token) >= 8:
            matches = self.conn.execute(
                "SELECT * FROM missions WHERE mission_id LIKE ?", (token + "%",)
            ).fetchall()
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise NotFoundError(f"mission not found: {token}")
        return dict(row)

    def resolve_task(self, token: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (token,)
        ).fetchone()
        if row is None and len(token) >= 8:
            matches = self.conn.execute(
                "SELECT * FROM tasks WHERE task_id LIKE ?", (token + "%",)
            ).fetchall()
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise NotFoundError(f"task not found: {token}")
        return dict(row)

    def resolve_feature(self, token: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM features WHERE feature_id = ?", (token,)
        ).fetchone()
        if row is None and len(token) >= 8:
            matches = self.conn.execute(
                "SELECT * FROM features WHERE feature_id LIKE ?", (token + "%",)
            ).fetchall()
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise NotFoundError(f"feature not found: {token}")
        return dict(row)

    def resolve_blocker(self, token: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM blockers WHERE blocker_id = ?", (token,)
        ).fetchone()
        if row is None and len(token) >= 8:
            matches = self.conn.execute(
                "SELECT * FROM blockers WHERE blocker_id LIKE ?", (token + "%",)
            ).fetchall()
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise NotFoundError(f"blocker not found: {token}")
        return dict(row)

    def resolve_session(self, token: str) -> dict[str, Any]:
        token = token.strip()
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (token,)
        ).fetchone()
        if row is None and len(token) >= 8:
            matches = self.conn.execute(
                "SELECT * FROM sessions WHERE session_id LIKE ?", (token + "%",)
            ).fetchall()
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise NotFoundError(f"session not found: {token}")
        return dict(row)

    def resolve_active_session(
        self,
        *,
        mission_id: str | None = None,
        actor: str | None = None,
        clank_id: str | None = None,
        session_id: str | None = None,
    ) -> str | None:
        """Bind a session without inventing one.

        Priority: explicit id, store default, unique open session for this
        actor on this mission. Ambiguous or missing stays unknown.
        """
        candidate = session_id or self.default_session_id
        if candidate:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (candidate,)
            ).fetchone()
            if row is None:
                return None
            if row["ended_utc"]:
                return None
            if mission_id and row["mission_id"] != mission_id:
                return None
            return row["session_id"]
        if not mission_id:
            return None
        actor = actor or self.default_actor
        matches = self.conn.execute(
            """
            SELECT session_id FROM sessions
            WHERE mission_id = ? AND actor = ? AND ended_utc IS NULL
            ORDER BY started_utc, session_id
            """,
            (mission_id, actor),
        ).fetchall()
        if len(matches) == 1:
            return matches[0][0]
        return None

    def find_clank_by_remote(self, remote: str) -> dict[str, Any] | None:
        if not remote:
            return None
        row = self.conn.execute(
            """
            SELECT c.* FROM clanks c
            JOIN clank_refs r ON r.clank_id = c.clank_id
            WHERE r.ref_kind IN ('git_remote', 'github_repo') AND r.ref_value = ?
            """,
            (remote,),
        ).fetchone()
        return dict(row) if row else None

    # --- clanks ---

    def register_clank(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        aliases: Iterable[str] = (),
        local_path: str | None = None,
        remotes: Iterable[str] = (),
        lifecycle: str | ClankLifecycle = ClankLifecycle.UNKNOWN,
        classification: str | None = None,
        actor: str | None = None,
        source: str | EventSource | None = None,
        first_known_utc: str | None = None,
        provenance: dict[str, Any] | None = None,
        extra_refs: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        slug = _slugify(slug)
        if not slug:
            raise ValidationError("slug is required")
        existing = self.conn.execute(
            "SELECT clank_id FROM clanks WHERE slug = ?", (slug,)
        ).fetchone()
        if existing:
            raise DuplicateError(f"clank already registered: {slug}")
        alias_row = self.conn.execute(
            "SELECT clank_id FROM clank_aliases WHERE alias = ?", (slug,)
        ).fetchone()
        if alias_row:
            raise DuplicateError(f"slug collides with existing alias: {slug}")
        clank_id = new_id()
        refs: list[dict[str, Any]] = []
        if local_path:
            refs.append({"kind": "local_path", "value": str(Path(local_path)), "canonical": True})
        for remote in remotes:
            if remote:
                refs.append({"kind": "git_remote", "value": remote, "canonical": True})
        refs.extend(list(extra_refs))
        alias_list = [_slugify(a) if a else "" for a in aliases]
        alias_list = [a for a in alias_list if a and a != slug]
        self._emit(
            EventType.CLANK_REGISTERED,
            {
                "slug": slug,
                "display_name": display_name or slug,
                "description": description,
                "aliases": alias_list,
                "lifecycle": str(lifecycle),
                "classification": classification,
                "first_known_utc": first_known_utc,
                "refs": refs,
            },
            actor=actor,
            source=source,
            clank_id=clank_id,
            provenance=provenance,
        )
        self.commit()
        return self.resolve_clank(clank_id)

    def add_alias(self, clank: str, alias: str, *, actor: str | None = None) -> Event:
        row = self.resolve_clank(clank)
        alias = _slugify(alias)
        if not alias:
            raise ValidationError("alias is required")
        taken = self.conn.execute(
            "SELECT clank_id FROM clank_aliases WHERE alias = ?", (alias,)
        ).fetchone()
        if taken and taken["clank_id"] != row["clank_id"]:
            raise DuplicateError(f"alias already used: {alias}")
        event = self._emit(
            EventType.CLANK_ALIAS_ADDED,
            {"alias": alias},
            actor=actor,
            clank_id=row["clank_id"],
        )
        self.commit()
        return event

    def update_ref(
        self,
        clank: str,
        kind: str,
        value: str,
        *,
        canonical: bool = False,
        replace_kind: bool = False,
        actor: str | None = None,
        source: str | EventSource | None = None,
    ) -> Event:
        row = self.resolve_clank(clank)
        event = self._emit(
            EventType.CLANK_REF_UPDATED,
            {"kind": kind, "value": value, "canonical": canonical, "replace_kind": replace_kind},
            actor=actor,
            source=source,
            clank_id=row["clank_id"],
        )
        self.commit()
        return event

    def set_lifecycle(self, clank: str, lifecycle: str, *, actor: str | None = None) -> Event:
        row = self.resolve_clank(clank)
        event = self._emit(
            EventType.CLANK_LIFECYCLE_CHANGED,
            {"lifecycle": lifecycle, "from_lifecycle": row["lifecycle"]},
            actor=actor,
            clank_id=row["clank_id"],
        )
        self.commit()
        return event

    def list_clanks(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM clanks ORDER BY slug COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def clank_detail(self, token: str) -> dict[str, Any]:
        clank = self.resolve_clank(token)
        aliases = [
            r[0]
            for r in self.conn.execute(
                "SELECT alias FROM clank_aliases WHERE clank_id = ? ORDER BY alias",
                (clank["clank_id"],),
            )
        ]
        refs = [
            dict(r)
            for r in self.conn.execute(
                "SELECT ref_kind, ref_value, is_canonical FROM clank_refs WHERE clank_id = ?",
                (clank["clank_id"],),
            )
        ]
        missions = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM missions WHERE clank_id = ? ORDER BY created_utc DESC",
                (clank["clank_id"],),
            )
        ]
        features = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM features WHERE clank_id = ? ORDER BY created_utc",
                (clank["clank_id"],),
            )
        ]
        relationships = [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT * FROM relationships
                WHERE from_clank_id = ? OR to_clank_id = ?
                ORDER BY created_utc
                """,
                (clank["clank_id"], clank["clank_id"]),
            )
        ]
        return {
            **clank,
            "aliases": aliases,
            "refs": refs,
            "missions": missions,
            "features": features,
            "relationships": relationships,
        }

    # --- missions / sessions ---

    def _next_display_id(self) -> str:
        issued = max_issued_mission_display_n(self.conn)
        row = self.conn.execute(
            "SELECT next_value FROM id_sequences WHERE name = 'mission_display'"
        ).fetchone()
        n = max(int(row[0]) if row else 1, issued + 1)
        self.conn.execute(
            """
            INSERT INTO id_sequences(name, next_value) VALUES ('mission_display', ?)
            ON CONFLICT(name) DO UPDATE SET next_value = excluded.next_value
            """,
            (n + 1,),
        )
        return format_mission_display_id(n)

    def start_mission(
        self,
        clank: str,
        objective: str,
        *,
        actor: str | None = None,
        source: str | EventSource | None = None,
        state: str = MissionState.ACTIVE,
    ) -> dict[str, Any]:
        if not objective.strip():
            raise ValidationError("mission objective is required")
        clank_row = self.resolve_clank(clank)
        mission_id = new_id()
        display_id = self._next_display_id()
        actor = actor or self.default_actor
        self._emit(
            EventType.MISSION_CREATED,
            {
                "display_id": display_id,
                "objective": objective.strip(),
                "state": str(state),
            },
            actor=actor,
            source=source,
            clank_id=clank_row["clank_id"],
            mission_id=mission_id,
            bind_session=False,
        )
        session = None
        if str(state) == MissionState.ACTIVE:
            session = self.start_session(
                mission_id, actor=actor, source=source, commit=False
            )
        self.commit()
        result = self.resolve_mission(mission_id)
        if session:
            result["session_id"] = session["session_id"]
        return result

    def start_session(
        self,
        mission: str,
        *,
        actor: str | None = None,
        source: str | EventSource | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        row = self.resolve_mission(mission)
        if row["state"] != MissionState.ACTIVE:
            raise InvalidTransitionError(
                f"cannot start a session unless the mission is ACTIVE (now {row['state']})"
            )
        session_id = new_id()
        actor = actor or self.default_actor
        self._emit(
            EventType.SESSION_STARTED,
            {"actor": actor},
            actor=actor,
            source=source,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
            bind_session=False,
        )
        if commit:
            self.commit()
        return dict(
            self.conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        )

    def end_session(
        self,
        session: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        row = self.resolve_session(session)
        if row["ended_utc"]:
            raise InvalidTransitionError(f"session already ended: {row['session_id']}")
        self._emit(
            EventType.SESSION_ENDED,
            {"reason": reason or "explicit"},
            actor=actor or self.default_actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=row["session_id"],
            bind_session=False,
        )
        if commit:
            self.commit()
        return self.resolve_session(row["session_id"])

    def _end_open_sessions(
        self,
        mission_id: str,
        *,
        actor: str | None = None,
        reason: str | None = None,
    ) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT session_id FROM sessions
            WHERE mission_id = ? AND ended_utc IS NULL
            ORDER BY started_utc, session_id
            """,
            (mission_id,),
        ).fetchall()
        ended: list[str] = []
        for row in rows:
            self.end_session(row[0], actor=actor, reason=reason, commit=False)
            ended.append(row[0])
        return ended

    def list_sessions(self, mission: str) -> list[dict[str, Any]]:
        row = self.resolve_mission(mission)
        return [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT * FROM sessions
                WHERE mission_id = ?
                ORDER BY started_utc, session_id
                """,
                (row["mission_id"],),
            )
        ]

    def transition_mission(
        self,
        mission: str,
        to_state: str | MissionState,
        *,
        actor: str | None = None,
        superseded_by: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        row = self.resolve_mission(mission)
        current = MissionState(row["state"])
        target = MissionState(to_state)
        if target not in MISSION_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"cannot transition mission {row['display_id']} from {current} to {target}"
            )
        if target in MISSION_WORK_STOPPED:
            self._end_open_sessions(
                row["mission_id"],
                actor=actor,
                reason=f"mission_{str(target).lower()}",
            )
        payload: dict[str, Any] = {
            "from_state": str(current),
            "to_state": str(target),
        }
        if reason:
            payload["reason"] = reason
        if superseded_by:
            payload["superseded_by"] = superseded_by
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload,
            actor=actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            bind_session=False,
        )
        self.commit()
        return self.resolve_mission(row["mission_id"])

    def pause_mission(self, mission: str, **kwargs: Any) -> dict[str, Any]:
        return self.transition_mission(mission, MissionState.PAUSED, **kwargs)

    def block_mission(self, mission: str, **kwargs: Any) -> dict[str, Any]:
        return self.transition_mission(mission, MissionState.BLOCKED, **kwargs)

    def resume_mission(self, mission: str, **kwargs: Any) -> dict[str, Any]:
        row = self.resolve_mission(mission)
        if row["state"] not in {MissionState.PAUSED, MissionState.BLOCKED, MissionState.PLANNED}:
            raise InvalidTransitionError(
                f"cannot resume mission {row['display_id']} from {row['state']}"
            )
        result = self.transition_mission(mission, MissionState.ACTIVE, **kwargs)
        session = self.start_session(
            row["mission_id"],
            actor=kwargs.get("actor"),
            source=kwargs.get("source"),
        )
        result["session_id"] = session["session_id"]
        return result

    def complete_mission(self, mission: str, **kwargs: Any) -> dict[str, Any]:
        return self.transition_mission(mission, MissionState.COMPLETED, **kwargs)

    def abandon_mission(self, mission: str, **kwargs: Any) -> dict[str, Any]:
        return self.transition_mission(mission, MissionState.ABANDONED, **kwargs)

    def list_missions(self, clank: str) -> list[dict[str, Any]]:
        row = self.resolve_clank(clank)
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM missions WHERE clank_id = ? ORDER BY created_utc DESC",
                (row["clank_id"],),
            )
        ]

    # --- checkpoint / task / feature / decision / blocker ---

    def record_checkpoint(
        self,
        mission: str,
        *,
        completed: str | None = None,
        current_work: str | None = None,
        next_action: str | None = None,
        outstanding: list[str] | None = None,
        blockers: list[str] | None = None,
        tests: str | None = None,
        branch: str | None = None,
        head: str | None = None,
        working_tree: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        notes: str | None = None,
        actor: str | None = None,
        source: str | EventSource | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.resolve_mission(mission)
        checkpoint_id = new_id()
        event = self._emit(
            EventType.CHECKPOINT_RECORDED,
            {
                "checkpoint_id": checkpoint_id,
                "completed": completed,
                "current_work": current_work,
                "next_action": next_action,
                "outstanding": outstanding or [],
                "blockers": blockers or [],
                "tests": tests,
                "branch": branch,
                "head": head,
                "working_tree": working_tree,
                "artifacts": artifacts or [],
                "notes": notes,
            },
            actor=actor,
            source=source,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
        )
        self.commit()
        cp = self.conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
        ).fetchone()
        return {**dict(cp), "event_id": event.event_id}

    def add_task(
        self, mission: str, title: str, *, actor: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValidationError("task title is required")
        row = self.resolve_mission(mission)
        task_id = new_id()
        self._emit(
            EventType.TASK_CREATED,
            {"task_id": task_id, "title": title.strip(), "state": TaskState.TODO},
            actor=actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
        )
        self.commit()
        return self.resolve_task(task_id)

    def transition_task(
        self,
        task: str,
        to_state: str | TaskState,
        *,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.resolve_task(task)
        current = TaskState(row["state"])
        target = TaskState(to_state)
        if target not in TASK_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"cannot transition task from {current} to {target}"
            )
        self._emit(
            EventType.TASK_STATE_CHANGED,
            {
                "task_id": row["task_id"],
                "from_state": str(current),
                "to_state": str(target),
            },
            actor=actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
        )
        self.commit()
        return self.resolve_task(row["task_id"])

    def add_feature(
        self,
        clank: str,
        name: str,
        *,
        state: str | FeatureState = FeatureState.PRESENT,
        actor: str | None = None,
        session_id: str | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValidationError("feature name is required")
        clank_row = self.resolve_clank(clank)
        feature_id = new_id()
        self._emit(
            EventType.FEATURE_ADDED,
            {"feature_id": feature_id, "name": name.strip(), "state": str(state)},
            actor=actor,
            clank_id=clank_row["clank_id"],
            mission_id=mission_id,
            session_id=session_id,
        )
        self.commit()
        return self.resolve_feature(feature_id)

    def transition_feature(
        self,
        feature: str,
        to_state: str | FeatureState,
        *,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.resolve_feature(feature)
        current = FeatureState(row["state"])
        target = FeatureState(to_state)
        if target not in FEATURE_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"cannot transition feature from {current} to {target}"
            )
        self._emit(
            EventType.FEATURE_STATE_CHANGED,
            {
                "feature_id": row["feature_id"],
                "from_state": str(current),
                "to_state": str(target),
            },
            actor=actor,
            clank_id=row["clank_id"],
            session_id=session_id,
        )
        self.commit()
        return self.resolve_feature(row["feature_id"])

    def add_decision(
        self,
        mission: str,
        statement: str,
        *,
        why: str | None = None,
        alternatives: str | None = None,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not statement.strip():
            raise ValidationError("decision statement is required")
        row = self.resolve_mission(mission)
        decision_id = new_id()
        self._emit(
            EventType.DECISION_RECORDED,
            {
                "decision_id": decision_id,
                "statement": statement.strip(),
                "why": why,
                "alternatives": alternatives,
            },
            actor=actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
        )
        self.commit()
        return dict(
            self.conn.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        )

    def add_blocker(
        self,
        mission: str,
        description: str,
        *,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not description.strip():
            raise ValidationError("blocker description is required")
        row = self.resolve_mission(mission)
        blocker_id = new_id()
        self._emit(
            EventType.BLOCKER_ADDED,
            {"blocker_id": blocker_id, "description": description.strip()},
            actor=actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
        )
        self.commit()
        return self.resolve_blocker(blocker_id)

    def resolve_open_blocker(
        self,
        blocker: str,
        *,
        resolution: str | None = None,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        row = self.resolve_blocker(blocker)
        if row["state"] != BlockerState.OPEN:
            raise InvalidTransitionError("blocker is not open")
        self._emit(
            EventType.BLOCKER_RESOLVED,
            {"blocker_id": row["blocker_id"], "resolution": resolution},
            actor=actor,
            clank_id=row["clank_id"],
            mission_id=row["mission_id"],
            session_id=session_id,
        )
        self.commit()
        return self.resolve_blocker(row["blocker_id"])

    def add_relationship(
        self,
        from_clank: str,
        kind: str,
        to_clank: str,
        *,
        metadata: dict[str, Any] | None = None,
        actor: str | None = None,
        source: str | EventSource | None = None,
    ) -> dict[str, Any]:
        if not kind.strip():
            raise ValidationError("relationship kind is required")
        src = self.resolve_clank(from_clank)
        dst = self.resolve_clank(to_clank)
        rel_id = new_id()
        self._emit(
            EventType.RELATIONSHIP_RECORDED,
            {
                "relationship_id": rel_id,
                "from_clank_id": src["clank_id"],
                "to_clank_id": dst["clank_id"],
                "kind": kind.strip().upper(),
                "metadata": metadata or {},
            },
            actor=actor,
            source=source,
            clank_id=src["clank_id"],
        )
        self.commit()
        return dict(
            self.conn.execute(
                "SELECT * FROM relationships WHERE relationship_id = ?", (rel_id,)
            ).fetchone()
        )

    def attach_artifact(
        self,
        *,
        clank: str | None = None,
        mission: str | None = None,
        kind: str,
        ref: str,
        title: str | None = None,
        artifact_source: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor: str | None = None,
        source: str | EventSource | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        clank_id = None
        mission_id = None
        if mission:
            m = self.resolve_mission(mission)
            clank_id = m["clank_id"]
            mission_id = m["mission_id"]
        elif clank:
            clank_id = self.resolve_clank(clank)["clank_id"]
        artifact_id = new_id()
        self._emit(
            EventType.ARTIFACT_ATTACHED,
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "ref": ref,
                "title": title,
                "artifact_source": artifact_source or str(source or self.default_source),
                "metadata": metadata or {},
            },
            actor=actor,
            source=source,
            clank_id=clank_id,
            mission_id=mission_id,
            session_id=session_id,
        )
        self.commit()
        return dict(
            self.conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        )

    # --- census import ---

    def record_census_candidate(
        self,
        candidate: dict[str, Any],
        *,
        actor: str = "system",
        source: str | EventSource = EventSource.RECONSTRUCTED,
    ) -> dict[str, Any]:
        candidate_id = candidate.get("candidate_id") or new_id()
        payload = {**candidate, "candidate_id": candidate_id}
        event = self._emit(
            EventType.CENSUS_CANDIDATE_RECORDED,
            payload,
            actor=actor,
            source=source,
            clank_id=candidate.get("clank_id"),
            provenance={
                "recorder": "clankops.census",
                "reconstructed": source == EventSource.RECONSTRUCTED
                or str(source) == EventSource.RECONSTRUCTED,
            },
        )
        self.commit()
        return {"candidate_id": candidate_id, "event_id": event.event_id}

    def import_census(
        self,
        census: dict[str, Any],
        *,
        actor: str = "system",
        source: str | EventSource = EventSource.RECONSTRUCTED,
    ) -> dict[str, int]:
        stats = {"candidates": 0, "registered": 0, "aliases": 0, "skipped_duplicates": 0}
        slug_to_id: dict[str, str] = {}
        for cand in census.get("candidates") or []:
            classification = cand.get("classification")
            slug = _slugify(cand.get("slug") or cand.get("name") or "")
            clank_id = None
            if (
                classification in {c.value for c in REGISTERABLE_CLASSIFICATIONS}
                or classification in REGISTERABLE_CLASSIFICATIONS
            ) and slug:
                existing = self.conn.execute(
                    "SELECT * FROM clanks WHERE slug = ?", (slug,)
                ).fetchone()
                if existing is None:
                    alias_hit = self.conn.execute(
                        "SELECT clank_id FROM clank_aliases WHERE alias = ?", (slug,)
                    ).fetchone()
                    if alias_hit:
                        existing = self.conn.execute(
                            "SELECT * FROM clanks WHERE clank_id = ?",
                            (alias_hit["clank_id"],),
                        ).fetchone()
                remote = cand.get("canonical_remote") or cand.get("remote")
                if existing is None and remote:
                    found = self.find_clank_by_remote(remote)
                    existing = (
                        self.conn.execute(
                            "SELECT * FROM clanks WHERE clank_id = ?",
                            (found["clank_id"],),
                        ).fetchone()
                        if found
                        else None
                    )
                if existing:
                    clank_id = existing["clank_id"]
                    stats["skipped_duplicates"] += 1
                    path = cand.get("local_path")
                    if path:
                        self.update_ref(
                            clank_id,
                            "local_path",
                            path,
                            actor=actor,
                            source=source,
                        )
                else:
                    remotes = []
                    if remote:
                        remotes.append(remote)
                    lifecycle = cand.get("lifecycle") or ClankLifecycle.UNKNOWN
                    if classification == CensusClassification.SUPPORT_COMPONENT:
                        lifecycle = ClankLifecycle.SUPPORT
                    registered = self.register_clank(
                        slug,
                        display_name=cand.get("display_name") or cand.get("name") or slug,
                        description=cand.get("apparent_purpose"),
                        aliases=cand.get("aliases") or [],
                        local_path=cand.get("local_path"),
                        remotes=remotes,
                        lifecycle=lifecycle,
                        classification=classification,
                        actor=actor,
                        source=source,
                        first_known_utc=cand.get("latest_commit_timestamp"),
                        provenance={
                            "recorder": "census_import",
                            "reconstructed": True,
                            "evidence": cand.get("evidence") or [],
                        },
                    )
                    clank_id = registered["clank_id"]
                    stats["registered"] += 1
                    for alias in cand.get("aliases") or []:
                        try:
                            self.add_alias(clank_id, alias, actor=actor)
                            stats["aliases"] += 1
                        except DuplicateError:
                            pass
                slug_to_id[slug] = clank_id
            cand = {**cand, "clank_id": clank_id}
            self.record_census_candidate(cand, actor=actor, source=source)
            stats["candidates"] += 1
        self.commit()
        return stats

    # --- brief / history ---

    def latest_checkpoint(self, clank_id: str, mission_id: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM checkpoints WHERE clank_id = ?"
        params: list[Any] = [clank_id]
        if mission_id:
            sql += " AND mission_id = ?"
            params.append(mission_id)
        sql += " ORDER BY recorded_utc DESC, checkpoint_id DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def active_or_unfinished_mission(self, clank_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM missions
            WHERE clank_id = ?
              AND state IN ('PLANNED', 'ACTIVE', 'PAUSED', 'BLOCKED')
            ORDER BY
                CASE state
                    WHEN 'ACTIVE' THEN 0
                    WHEN 'BLOCKED' THEN 1
                    WHEN 'PAUSED' THEN 2
                    ELSE 3
                END,
                updated_utc DESC
            LIMIT 1
            """,
            (clank_id,),
        ).fetchone()
        if row:
            return dict(row)
        row = self.conn.execute(
            """
            SELECT * FROM missions WHERE clank_id = ?
            ORDER BY updated_utc DESC LIMIT 1
            """,
            (clank_id,),
        ).fetchone()
        return dict(row) if row else None

    def brief(self, clank: str) -> dict[str, Any]:
        detail = self.clank_detail(clank)
        cid = detail["clank_id"]
        mission = self.active_or_unfinished_mission(cid)
        checkpoint = None
        tasks: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        if mission:
            checkpoint = self.latest_checkpoint(cid, mission["mission_id"])
            tasks = [
                dict(r)
                for r in self.conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE mission_id = ? AND state NOT IN ('DONE', 'CANCELLED')
                    ORDER BY created_utc
                    """,
                    (mission["mission_id"],),
                )
            ]
            blockers = [
                dict(r)
                for r in self.conn.execute(
                    """
                    SELECT * FROM blockers
                    WHERE mission_id = ? AND state = 'OPEN'
                    ORDER BY created_utc
                    """,
                    (mission["mission_id"],),
                )
            ]
            decisions = [
                dict(r)
                for r in self.conn.execute(
                    """
                    SELECT * FROM decisions
                    WHERE mission_id = ?
                    ORDER BY created_utc DESC
                    LIMIT 5
                    """,
                    (mission["mission_id"],),
                )
            ]
        path_ref = next((r for r in detail["refs"] if r["ref_kind"] == "local_path"), None)
        branch = checkpoint["branch"] if checkpoint else None
        head = checkpoint["head"] if checkpoint else None
        tests = checkpoint["tests"] if checkpoint else None
        next_action = checkpoint["next_action"] if checkpoint else None
        completed = checkpoint["completed"] if checkpoint else None
        return {
            "identity": {
                "clank_id": cid,
                "slug": detail["slug"],
                "display_name": detail["display_name"],
                "aliases": detail["aliases"],
                "description": detail["description"],
                "classification": detail["classification"],
                "local_path": path_ref["ref_value"] if path_ref else None,
            },
            "lifecycle_state": detail["lifecycle"],
            "mission": mission,
            "checkpoint": checkpoint,
            "completed_work": completed,
            "outstanding_tasks": tasks,
            "blockers": blockers,
            "branch": branch,
            "head": head,
            "tests": tests,
            "decisions": decisions,
            "next_action": next_action,
            "features": detail["features"],
        }

    def history(self, clank: str, *, limit: int | None = None) -> list[Event]:
        row = self.resolve_clank(clank)
        return list_events(self.conn, clank_id=row["clank_id"], limit=limit)

    def rebuild(self) -> int:
        return rebuild_projections(self.conn)

    def projection_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return dump_projection_state(self.conn)


def open_store(
    path: str | Path,
    *,
    actor: str = "user",
    source: str = EventSource.USER,
    clock: Clock | None = None,
    session_id: str | None = None,
) -> Store:
    from clankops.db import connect

    clock = clock or SystemClock()
    conn = connect(path, clock=clock)
    return Store(
        conn=conn,
        clock=clock,
        default_actor=actor,
        default_source=source,
        default_session_id=session_id,
    )
