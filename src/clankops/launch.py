"""Managed agent launch gate. Prepare, admit, then spawn. Fail closed.

Does not invent Missions, handoffs, or next actions. Child exit is not a
Mission completion. Actor and launcher names are provenance, not permission.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from clankops.agent import admit_agent, prepare_agent, require_actor
from clankops.enums import EventType
from clankops.errors import ValidationError
from clankops.resume import (
    STATUS_AMBIGUOUS,
    STATUS_NO_UNFINISHED_MISSION,
    STATUS_RESUMABLE,
)
from clankops.store import Store

RunCommand = Callable[..., subprocess.CompletedProcess[Any]]


def require_launcher(launcher: str | None) -> str:
    name = (launcher or "").strip()
    if not name:
        raise ValidationError("launcher identity is required (provenance, not a permission)")
    return name


def require_command(command: Sequence[str] | None) -> list[str]:
    argv = [str(part) for part in (command or [])]
    if not argv:
        raise ValidationError(
            "agent command is required; refusing to admit without a process to launch"
        )
    return argv


def _select_mission(packet: dict[str, Any], mission: str | None) -> str:
    admission = packet.get("admission") or {}
    status = admission.get("status")
    explicit = (mission or "").strip() or None
    if status == STATUS_AMBIGUOUS:
        if not explicit:
            listed = ", ".join(
                f"{row.get('mission')} [{row.get('state')}]"
                for row in (packet.get("unfinished_missions") or [])
            )
            raise ValidationError(
                f"AMBIGUOUS unfinished Missions ({listed}). "
                "Pass --mission COPS-xxxxxx; never silently pick one."
            )
        return explicit
    if status == STATUS_NO_UNFINISHED_MISSION:
        slug = (packet.get("identity") or {}).get("slug") or "this Clank"
        raise ValidationError(
            f"NO_UNFINISHED_MISSION for {slug}. "
            "Use work start for a new objective; launch will not create a Mission."
        )
    if status == STATUS_RESUMABLE:
        return explicit or admission.get("resumable_mission")
    raise ValidationError(f"unusable admission status: {status or 'unknown'}")


def open_session_ids_for_actor(store: Store, *, mission_id: str, actor: str) -> list[str]:
    rows = store.conn.execute(
        """
        SELECT session_id FROM sessions
        WHERE mission_id = ? AND actor = ? AND ended_utc IS NULL
        ORDER BY started_utc, session_id
        """,
        (mission_id, actor),
    ).fetchall()
    return [row["session_id"] for row in rows]


def _refuse_existing_open_session(store: Store, *, mission: str, actor: str) -> None:
    row = store.resolve_mission(mission)
    existing = open_session_ids_for_actor(
        store, mission_id=row["mission_id"], actor=actor
    )
    if not existing:
        return
    listed = ", ".join(existing)
    raise ValidationError(
        f"open Session already exists for actor {actor} on {row['display_id']}: {listed}. "
        "Handoff or end that Session through Foundation 1 before another managed launch. "
        "Refusing to reuse a Session that would not carry this launch's provenance."
    )


def _require_launch_session(
    store: Store,
    *,
    session_id: str,
    actor: str,
    launcher: str,
    context_fingerprint: str,
    mission_id: str,
    started_before: int,
) -> dict[str, Any]:
    started_after = store.conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = ?",
        (EventType.SESSION_STARTED,),
    ).fetchone()["n"]
    if int(started_after) != started_before + 1:
        raise ValidationError(
            "managed launch did not emit SESSION_STARTED; refusing to claim "
            "launcher/fingerprint provenance for a reused Session"
        )
    events = store.conn.execute(
        """
        SELECT payload_json, provenance_json, source, actor, mission_id, session_id
        FROM events
        WHERE event_type = ? AND session_id = ?
        """,
        (EventType.SESSION_STARTED, session_id),
    ).fetchall()
    if len(events) != 1:
        raise ValidationError(
            f"managed launch requires exactly one SESSION_STARTED for {session_id}"
        )
    event = events[0]
    payload = json.loads(event["payload_json"] or "{}")
    provenance = json.loads(event["provenance_json"] or "{}")
    session = dict(store.resolve_session(session_id))
    if event["actor"] != actor or session.get("actor") != actor:
        raise ValidationError("launch Session actor does not match the requesting actor")
    if event["mission_id"] != mission_id or session.get("mission_id") != mission_id:
        raise ValidationError("launch Session mission does not match the admitted Mission")
    if (payload.get("launcher") or provenance.get("launcher")) != launcher:
        raise ValidationError("SESSION_STARTED launcher does not match this launch")
    if (payload.get("context_fingerprint") or provenance.get("context_fingerprint")) != context_fingerprint:
        raise ValidationError("SESSION_STARTED context fingerprint does not match this launch")
    if not event["source"] or session.get("source") != event["source"]:
        raise ValidationError("launch Session source is missing from provenance")
    if session.get("launcher") != launcher:
        raise ValidationError("projected Session launcher does not match this launch")
    if session.get("context_fingerprint") != context_fingerprint:
        raise ValidationError("projected Session fingerprint does not match this launch")
    if session.get("ended_utc") is not None:
        raise ValidationError("managed launch Session is already closed")
    return session


def child_environ(
    base: Mapping[str, str],
    *,
    clank_slug: str,
    clank_id: str,
    mission_display: str,
    mission_id: str,
    session_id: str,
    actor: str,
    launcher: str,
    context_fingerprint: str,
    db: str | None = None,
) -> dict[str, str]:
    env = {str(key): str(value) for key, value in base.items() if value is not None}
    overlay = {
        "CLANKOPS_CLANK": clank_slug,
        "CLANKOPS_CLANK_SLUG": clank_slug,
        "CLANKOPS_CLANK_ID": clank_id,
        "CLANKOPS_MISSION": mission_display,
        "CLANKOPS_MISSION_DISPLAY": mission_display,
        "CLANKOPS_MISSION_ID": mission_id,
        "CLANKOPS_SESSION_ID": session_id,
        "CLANKOPS_ACTOR": actor,
        "CLANKOPS_LAUNCHER": launcher,
        "CLANKOPS_CONTEXT_FINGERPRINT": context_fingerprint,
    }
    if db:
        overlay["CLANKOPS_DB"] = db
    env.update(overlay)
    return env


def launch_agent(
    store: Store,
    clank: str,
    *,
    actor: str | None,
    launcher: str | None,
    command: Sequence[str] | None,
    mission: str | None = None,
    expect_context: str | None = None,
    source: str | None = None,
    now: datetime | None = None,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
    environ: Mapping[str, str] | None = None,
    runner: RunCommand | None = None,
    cwd: str | None = None,
    db: str | None = None,
) -> dict[str, Any]:
    """Prepare, admit, then invoke the agent process. Fail before spawn on error."""
    requested = require_actor(actor)
    launcher_id = require_launcher(launcher)
    argv = require_command(command)
    packet = prepare_agent(
        store,
        clank,
        actor=requested,
        now=now,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    selected = _select_mission(packet, mission)
    expected = (expect_context or "").strip() or packet["context_fingerprint"]
    _refuse_existing_open_session(store, mission=selected, actor=requested)
    started_before = int(
        store.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE event_type = ?",
            (EventType.SESSION_STARTED,),
        ).fetchone()["n"]
    )
    admitted = admit_agent(
        store,
        clank,
        selected,
        actor=requested,
        expect_context=expected,
        source=source,
        launcher=launcher_id,
        now=now,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    session_id = admitted.get("session_id")
    if not session_id:
        raise ValidationError("admission returned no Session identity; refusing to launch")
    _require_launch_session(
        store,
        session_id=str(session_id),
        actor=requested,
        launcher=launcher_id,
        context_fingerprint=str(admitted["context_fingerprint"]),
        mission_id=str(admitted["mission_id"]),
        started_before=started_before,
    )
    ident = packet.get("identity") or {}
    child_env = child_environ(
        environ if environ is not None else os.environ,
        clank_slug=str(ident.get("slug") or clank),
        clank_id=str(ident.get("clank_id") or ""),
        mission_display=str(admitted["mission"]),
        mission_id=str(admitted["mission_id"]),
        session_id=str(session_id),
        actor=requested,
        launcher=launcher_id,
        context_fingerprint=str(admitted["context_fingerprint"]),
        db=db,
    )
    run = runner or subprocess.run
    completed = run(argv, env=child_env, check=False, shell=False, cwd=cwd)
    exit_code = getattr(completed, "returncode", None)
    return {
        "launched": True,
        "argv": argv,
        "exit_code": exit_code,
        "requested_actor": requested,
        "launcher": launcher_id,
        "mission": admitted["mission"],
        "mission_id": admitted["mission_id"],
        "mission_state": admitted["mission_state"],
        "session_id": session_id,
        "context_fingerprint": admitted["context_fingerprint"],
        "packet": packet,
        "env": {
            key: child_env[key]
            for key in (
                "CLANKOPS_CLANK",
                "CLANKOPS_MISSION",
                "CLANKOPS_MISSION_ID",
                "CLANKOPS_SESSION_ID",
                "CLANKOPS_CONTEXT_FINGERPRINT",
                "CLANKOPS_ACTOR",
                "CLANKOPS_LAUNCHER",
            )
            if key in child_env
        },
    }
