"""Managed agent process lifecycle evidence.

Process exit is an immutable observation. It is not a handoff, Mission
transition, checkpoint, or next_action. Missing evidence stays UNKNOWN;
it is never treated as RUNNING.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from clankops.clock import isoformat_utc
from clankops.enums import EventSource, EventType
from clankops.errors import ValidationError
from clankops.ids import new_id
from clankops.redact import sanitize_captured, sanitize_text
from clankops.store import Store
from clankops.timefmt import format_age, parse_utc

KIND_EXITED = "EXITED"
KIND_START_FAILED = "START_FAILED"
STATUS_UNKNOWN = "UNKNOWN"
SESSION_OPEN = "OPEN"
SESSION_CLOSED = "CLOSED"
HANDOFF_MISSING = "MISSING"
HANDOFF_RECORDED = "RECORDED"
HANDOFF_UNKNOWN = "UNKNOWN"
OBSERVED_HOW_PARENT_WAIT = "parent_wait"
OBSERVED_HOW_SPAWN_EXCEPTION = "spawn_exception"
RECORDER = "clankops.launch"
PROMPT_LIKE_CHARS = 200
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "env",
        "environ",
        "environment",
        "argv",
        "args",
        "command",
        "argv_summary",
        "shell",
    }
)


def argument_is_secret_shaped(value: str) -> bool:
    """True when an argv token looks like a secret, prompt, or pasted source."""
    text = str(value)
    if len(text) > PROMPT_LIKE_CHARS:
        return True
    cleaned = sanitize_text(text)
    if cleaned != text:
        return True
    stripped = text.lstrip("-")
    if "=" in stripped:
        key, _, rest = stripped.partition("=")
        probed = sanitize_captured({key: rest})
        redacted = probed.get(key)
        if redacted == "[redacted]":
            return True
        if isinstance(redacted, str) and redacted != rest:
            return True
        if rest and argument_is_secret_shaped(rest):
            return True
    return False


def executable_basename(argv0: str | None) -> str | None:
    """Basename of argv[0]. Accepts Windows or POSIX separators."""
    text = str(argv0 or "").strip()
    if not text:
        return None
    name = Path(text.replace("\\", "/")).name.strip()
    return sanitize_text(name) if name else None


def command_identity(argv: Sequence[str] | None) -> dict[str, Any]:
    """Safe command identity: executable basename, count, redaction flag.

    Never returns argv, a shell string, or inherited environment.
    """
    parts = [str(part) for part in (argv or [])]
    executable = executable_basename(parts[0]) if parts else None
    return {
        "executable": executable,
        "argv_count": len(parts),
        "argv_redacted": any(argument_is_secret_shaped(part) for part in parts),
    }


def _require_session(store: Store, session_id: str) -> dict[str, Any]:
    row = store.resolve_session(session_id)
    if not row.get("session_id"):
        raise ValidationError("process observation requires a Session")
    return dict(row)


def _payload(*, forbidden: dict[str, Any]) -> dict[str, Any]:
    leaked = FORBIDDEN_PAYLOAD_KEYS.intersection(forbidden)
    if leaked:
        raise ValidationError(f"process observation must not persist {sorted(leaked)}")
    return forbidden


def observations_for_session(store: Store, session_id: str) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        """
        SELECT * FROM agent_process_observations
        WHERE session_id = ?
        ORDER BY ledger_seq, observation_id
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def observations_for_clank(store: Store, clank_id: str) -> list[dict[str, Any]]:
    rows = store.conn.execute(
        """
        SELECT * FROM agent_process_observations
        WHERE clank_id = ?
        ORDER BY ledger_seq, observation_id
        """,
        (clank_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_observation(store: Store, session_id: str) -> dict[str, Any] | None:
    rows = observations_for_session(store, session_id)
    return rows[-1] if rows else None


def observation_facts(row: dict[str, Any]) -> dict[str, Any]:
    """Immutable observation facts. No display age."""
    return {
        "observation_id": row.get("observation_id"),
        "session_id": row.get("session_id"),
        "mission_id": row.get("mission_id"),
        "clank_id": row.get("clank_id"),
        "kind": row.get("kind"),
        "actor": row.get("actor"),
        "launcher": row.get("launcher"),
        "context_fingerprint": row.get("context_fingerprint"),
        "executable": row.get("executable"),
        "argv_count": row.get("argv_count"),
        "argv_redacted": bool(row.get("argv_redacted")),
        "exit_code": row.get("exit_code"),
        "error": row.get("error"),
        "observed_at": row.get("observed_at"),
        "observed_how": row.get("observed_how"),
        "observer": row.get("observer"),
        "source": row.get("source"),
        "ledger_seq": row.get("ledger_seq"),
    }


def observation_facts_for_clank(store: Store, clank_id: str) -> list[dict[str, Any]]:
    return [observation_facts(row) for row in observations_for_clank(store, clank_id)]


def derive_handoff_status(store: Store, session: dict[str, Any]) -> str:
    """Handoff is proven from event history. Session close is not a handoff."""
    if session.get("ended_utc") is None:
        return HANDOFF_MISSING
    session_id = str(session.get("session_id") or "")
    if not session_id:
        return HANDOFF_UNKNOWN
    ended = store.conn.execute(
        """
        SELECT payload_json, ledger_seq FROM events
        WHERE event_type = ? AND session_id = ?
        ORDER BY ledger_seq DESC, event_id DESC
        LIMIT 1
        """,
        (str(EventType.SESSION_ENDED), session_id),
    ).fetchone()
    if ended is None:
        return HANDOFF_UNKNOWN
    try:
        payload = json.loads(ended["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    reason = str(payload.get("reason") or "")
    if not reason.startswith("mission_"):
        return HANDOFF_UNKNOWN
    checkpoint = store.conn.execute(
        """
        SELECT 1 FROM events
        WHERE event_type = ? AND session_id = ? AND ledger_seq < ?
        LIMIT 1
        """,
        (str(EventType.CHECKPOINT_RECORDED), session_id, ended["ledger_seq"]),
    ).fetchone()
    if checkpoint is None:
        return HANDOFF_UNKNOWN
    return HANDOFF_RECORDED


def session_process_view(
    store: Store,
    session: dict[str, Any],
    *,
    now,
) -> dict[str, Any]:
    """Derived Session process view. Missing evidence is UNKNOWN, never RUNNING."""
    session_id = str(session.get("session_id") or "")
    open_ = session.get("ended_utc") is None
    latest = latest_observation(store, session_id) if session_id else None
    history = observations_for_session(store, session_id) if session_id else []
    if latest is None:
        status = STATUS_UNKNOWN
        process_age = None
        age_seconds = None
    else:
        status = str(latest.get("kind") or STATUS_UNKNOWN)
        observed = parse_utc(latest.get("observed_at"))
        if observed is None:
            process_age = None
            age_seconds = None
        else:
            delta = now - observed
            process_age = format_age(delta)
            age_seconds = int(delta.total_seconds())
    return {
        "status": status,
        "session": SESSION_OPEN if open_ else SESSION_CLOSED,
        "handoff": derive_handoff_status(store, session),
        "kind": latest.get("kind") if latest else None,
        "exit_code": latest.get("exit_code") if latest else None,
        "error": latest.get("error") if latest else None,
        "observed_at": latest.get("observed_at") if latest else None,
        "observed_how": latest.get("observed_how") if latest else None,
        "process_age": process_age,
        "process_age_seconds": age_seconds,
        "executable": latest.get("executable") if latest else None,
        "argv_count": latest.get("argv_count") if latest else None,
        "argv_redacted": bool(latest.get("argv_redacted")) if latest else False,
        "observation_id": latest.get("observation_id") if latest else None,
        "observation_count": len(history),
        "actor": (latest.get("actor") if latest else None) or session.get("actor"),
        "launcher": (latest.get("launcher") if latest else None) or session.get("launcher"),
        "context_fingerprint": (
            (latest.get("context_fingerprint") if latest else None)
            or session.get("context_fingerprint")
        ),
        "history": [observation_facts(row) for row in history],
    }


def record_process_exited(
    store: Store,
    *,
    session_id: str,
    exit_code: int,
    argv: Sequence[str] | None,
    actor: str,
    launcher: str,
    context_fingerprint: str,
    commit: bool = True,
) -> dict[str, Any]:
    session = _require_session(store, session_id)
    identity = command_identity(argv)
    observation_id = new_id()
    observed_at = isoformat_utc(store.clock.now())
    payload = _payload(
        forbidden={
            "observation_id": observation_id,
            "kind": KIND_EXITED,
            "session_id": session["session_id"],
            "mission_id": session.get("mission_id"),
            "clank_id": session.get("clank_id"),
            "actor": actor,
            "launcher": launcher,
            "context_fingerprint": context_fingerprint,
            "executable": identity["executable"],
            "argv_count": identity["argv_count"],
            "argv_redacted": identity["argv_redacted"],
            "exit_code": int(exit_code),
            "observed_at": observed_at,
            "observed_how": OBSERVED_HOW_PARENT_WAIT,
            "observer": RECORDER,
            "source": str(EventSource.SYSTEM),
        }
    )
    event = store._emit(
        EventType.AGENT_PROCESS_EXITED,
        payload,
        actor=actor,
        source=EventSource.SYSTEM,
        clank_id=session.get("clank_id"),
        mission_id=session.get("mission_id"),
        session_id=session["session_id"],
        provenance={
            "recorder": RECORDER,
            "observed_how": OBSERVED_HOW_PARENT_WAIT,
            "launcher": launcher,
            "context_fingerprint": context_fingerprint,
        },
        bind_session=False,
    )
    if commit:
        store.commit()
    latest = latest_observation(store, session["session_id"])
    if latest is None:
        raise ValidationError("process-exit observation did not project")
    latest["event_id"] = event.event_id
    latest["ledger_seq"] = event.ledger_seq
    return latest


def record_process_start_failed(
    store: Store,
    *,
    session_id: str,
    argv: Sequence[str] | None,
    actor: str,
    launcher: str,
    context_fingerprint: str,
    error: str,
    commit: bool = True,
) -> dict[str, Any]:
    session = _require_session(store, session_id)
    identity = command_identity(argv)
    observation_id = new_id()
    observed_at = isoformat_utc(store.clock.now())
    error_name = (error or "").strip() or "OSError"
    payload = _payload(
        forbidden={
            "observation_id": observation_id,
            "kind": KIND_START_FAILED,
            "session_id": session["session_id"],
            "mission_id": session.get("mission_id"),
            "clank_id": session.get("clank_id"),
            "actor": actor,
            "launcher": launcher,
            "context_fingerprint": context_fingerprint,
            "executable": identity["executable"],
            "argv_count": identity["argv_count"],
            "argv_redacted": identity["argv_redacted"],
            "error": error_name,
            "observed_at": observed_at,
            "observed_how": OBSERVED_HOW_SPAWN_EXCEPTION,
            "observer": RECORDER,
            "source": str(EventSource.SYSTEM),
        }
    )
    event = store._emit(
        EventType.AGENT_PROCESS_START_FAILED,
        payload,
        actor=actor,
        source=EventSource.SYSTEM,
        clank_id=session.get("clank_id"),
        mission_id=session.get("mission_id"),
        session_id=session["session_id"],
        provenance={
            "recorder": RECORDER,
            "observed_how": OBSERVED_HOW_SPAWN_EXCEPTION,
            "launcher": launcher,
            "context_fingerprint": context_fingerprint,
        },
        bind_session=False,
    )
    if commit:
        store.commit()
    latest = latest_observation(store, session["session_id"])
    if latest is None:
        raise ValidationError("process start-failure observation did not project")
    latest["event_id"] = event.event_id
    latest["ledger_seq"] = event.ledger_seq
    return latest
