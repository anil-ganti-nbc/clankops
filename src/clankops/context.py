"""On-disk Session context for Cursor and crash survivability.

Chat memory is not the record. A development Session writes a small JSON/env
pair under ``%USERPROFILE%\\.clankops`` so a later process can resume without
the original conversation. Invalid context fails loudly; it does not block
emergency manual work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from clankops.errors import ValidationError

ENV_KEYS = (
    "CLANKOPS_DB",
    "CLANKOPS_ACTOR",
    "CLANKOPS_CLANK_ID",
    "CLANKOPS_CLANK_SLUG",
    "CLANKOPS_MISSION_ID",
    "CLANKOPS_MISSION_DISPLAY",
    "CLANKOPS_SESSION_ID",
    "CLANKOPS_CONTEXT_FILE",
)

RECOVERY_HINT = (
    "Emergency coding is not blocked. Do not export CLANKOPS_SESSION_ID "
    "until this is fixed. Recovery: python -m clankops --actor cursor work resume <clank>"
)


def context_dir() -> Path:
    override = os.environ.get("CLANKOPS_HOME")
    if override:
        return Path(override)
    return Path.home() / ".clankops"


def active_context_path() -> Path:
    return context_dir() / "active-context.json"


def active_env_path() -> Path:
    return context_dir() / "active-context.env"


def session_snapshot_path(session_id: str) -> Path:
    return context_dir() / "sessions" / f"{session_id}.json"


def build_context(
    *,
    db: str | Path,
    actor: str,
    clank: dict[str, Any],
    mission: dict[str, Any],
    session: dict[str, Any],
    git: dict[str, Any] | None = None,
    brief: str | None = None,
) -> dict[str, Any]:
    db_s = str(Path(db))
    payload = {
        "db": db_s,
        "actor": actor,
        "clank_id": clank["clank_id"],
        "clank_slug": clank.get("slug"),
        "mission_id": mission["mission_id"],
        "mission_display": mission.get("display_id"),
        "mission_state": mission.get("state"),
        "mission_objective": mission.get("objective"),
        "session_id": session["session_id"],
        "session_ended": bool(session.get("ended_utc")),
        "local_path": clank.get("local_path"),
        "git": git or {},
        "brief": brief,
        "context_file": str(active_context_path()),
    }
    payload["env"] = {
        "CLANKOPS_DB": db_s,
        "CLANKOPS_ACTOR": actor,
        "CLANKOPS_CLANK_ID": payload["clank_id"],
        "CLANKOPS_CLANK_SLUG": payload["clank_slug"] or "",
        "CLANKOPS_MISSION_ID": payload["mission_id"],
        "CLANKOPS_MISSION_DISPLAY": payload["mission_display"] or "",
        "CLANKOPS_SESSION_ID": payload["session_id"],
        "CLANKOPS_CONTEXT_FILE": payload["context_file"],
    }
    return payload


def write_context(payload: dict[str, Any]) -> Path:
    path = active_context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    env_path = active_env_path()
    lines = [f"{key}={payload['env'][key]}" for key in ENV_KEYS if payload.get("env", {}).get(key)]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sid = payload.get("session_id")
    if sid:
        snap = session_snapshot_path(sid)
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_context(path: Path | None = None) -> dict[str, Any] | None:
    target = path or active_context_path()
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def format_powershell_env(payload: dict[str, Any]) -> str:
    env = payload.get("env") or {}
    return "\n".join(f"$env:{key} = '{env[key]}'" for key in ENV_KEYS if env.get(key))


def env_from_os() -> dict[str, str | None]:
    return {key: (os.environ.get(key) or None) for key in ENV_KEYS}


def validate_session_env(
    store: Any,
    *,
    session_id: str | None,
    mission_id: str | None = None,
    clank_id: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Fail loudly if exported Session context is missing or contradictory."""
    if not session_id:
        raise ValidationError(
            "CLANKOPS_SESSION_ID is not set. " + RECOVERY_HINT
        )
    row = store.validate_session_for_mutation(
        session_id,
        mission_id=mission_id,
        clank_id=clank_id,
        actor=actor,
    )
    if mission_id and row["mission_id"] != mission_id:
        raise ValidationError(
            f"CLANKOPS_MISSION_ID does not match Session {row['session_id']}. " + RECOVERY_HINT
        )
    if clank_id and row["clank_id"] != clank_id:
        raise ValidationError(
            f"CLANKOPS_CLANK_ID does not match Session {row['session_id']}. " + RECOVERY_HINT
        )
    return row
