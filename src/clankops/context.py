"""On-disk Session context for Cursor and crash survivability.

Chat memory is not the record. Each Clank has its own context file so two
open workspaces cannot share a Session by accident. Invalid context fails
loudly; it does not block emergency manual work.
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
    "CLANKOPS_CLANK_PATH",
    "CLANKOPS_CONTEXT_FILE",
)

ACTIVE_ENV_KEYS = (
    "CLANKOPS_CLANK_ID",
    "CLANKOPS_CLANK_SLUG",
    "CLANKOPS_MISSION_ID",
    "CLANKOPS_MISSION_DISPLAY",
    "CLANKOPS_SESSION_ID",
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


def last_active_path() -> Path:
    return context_dir() / "last-active.json"


def clank_context_path(clank_id: str) -> Path:
    return context_dir() / "contexts" / f"{clank_id}.json"


def clank_env_path(clank_id: str) -> Path:
    return context_dir() / "contexts" / f"{clank_id}.env"


def session_snapshot_path(session_id: str) -> Path:
    return context_dir() / "sessions" / f"{session_id}.json"


def active_context_path() -> Path:
    """Deprecated global pointer. Never sufficient proof of workspace identity."""
    return last_active_path()


def active_env_path() -> Path:
    return context_dir() / "active-context.env"


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
    ended = bool(session.get("ended_utc"))
    ctx_file = str(clank_context_path(clank["clank_id"]))
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
        "session_ended": ended,
        "active": not ended,
        "local_path": clank.get("local_path"),
        "git": git or {},
        "brief": brief,
        "context_file": ctx_file,
    }
    env = {
        "CLANKOPS_DB": db_s,
        "CLANKOPS_ACTOR": actor,
        "CLANKOPS_CLANK_ID": payload["clank_id"],
        "CLANKOPS_CLANK_SLUG": payload["clank_slug"] or "",
        "CLANKOPS_MISSION_ID": payload["mission_id"],
        "CLANKOPS_MISSION_DISPLAY": payload["mission_display"] or "",
        "CLANKOPS_CONTEXT_FILE": ctx_file,
    }
    if payload.get("local_path"):
        env["CLANKOPS_CLANK_PATH"] = str(payload["local_path"])
    if not ended:
        env["CLANKOPS_SESSION_ID"] = payload["session_id"]
    payload["env"] = env
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_env_file(path: Path, env: dict[str, str], *, include_session: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key in ENV_KEYS:
        if key == "CLANKOPS_SESSION_ID" and not include_session:
            continue
        value = env.get(key)
        if value:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_context(payload: dict[str, Any], *, advertise_active: bool | None = None) -> Path:
    ended = bool(payload.get("session_ended"))
    if advertise_active is None:
        advertise_active = not ended
    clank_id = payload["clank_id"]
    ctx_path = clank_context_path(clank_id)
    payload = {**payload, "context_file": str(ctx_path), "active": advertise_active and not ended}
    if "env" in payload:
        payload["env"] = {**payload["env"], "CLANKOPS_CONTEXT_FILE": str(ctx_path)}
        if ended or not advertise_active:
            payload["env"].pop("CLANKOPS_SESSION_ID", None)
    _write_json(ctx_path, payload)
    sid = payload.get("session_id")
    if sid:
        _write_json(session_snapshot_path(sid), payload)
    include_session = advertise_active and not ended
    _write_env_file(clank_env_path(clank_id), payload.get("env") or {}, include_session=include_session)
    if include_session:
        _write_json(last_active_path(), {"clank_id": clank_id, "context_file": str(ctx_path)})
        _write_env_file(active_env_path(), payload.get("env") or {}, include_session=True)
    else:
        # Closed Session must not remain advertised as the global active env.
        env_path = active_env_path()
        if env_path.exists():
            current = env_path.read_text(encoding="utf-8")
            if payload.get("session_id") and payload["session_id"] in current:
                env_path.unlink()
        last = read_json(last_active_path())
        if last and last.get("clank_id") == clank_id:
            last_active_path().unlink(missing_ok=True)
    return ctx_path


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_clank_context(clank_id: str) -> dict[str, Any] | None:
    return read_json(clank_context_path(clank_id))


def read_context(path: Path | None = None) -> dict[str, Any] | None:
    if path is not None:
        return read_json(path)
    last = read_json(last_active_path())
    if last and last.get("clank_id"):
        return read_clank_context(last["clank_id"])
    legacy = context_dir() / "active-context.json"
    return read_json(legacy)


def format_powershell_env(payload: dict[str, Any]) -> str:
    env = payload.get("env") or {}
    lines = [f"$env:{key} = '{env[key]}'" for key in ENV_KEYS if env.get(key)]
    if "CLANKOPS_SESSION_ID" not in env:
        lines.append("Remove-Item Env:CLANKOPS_SESSION_ID -ErrorAction SilentlyContinue")
    return "\n".join(lines)


def env_from_os() -> dict[str, str | None]:
    return {key: (os.environ.get(key) or None) for key in ENV_KEYS}


def is_clankops_tool_checkout(path: str | Path) -> bool:
    """True when this directory is the ClankOps CLI repo, not a product workspace.

    `work env` may be invoked from the tool checkout after resume; that must
    not impersonate the ClankOps product identity unless the Session is for it.
    """
    root = Path(path)
    try:
        root = root.resolve()
    except OSError:
        root = root.absolute()
    env_root = os.environ.get("CLANKOPS_ROOT")
    if env_root:
        try:
            if Path(env_root).resolve() == root:
                return True
        except OSError:
            pass
    return (root / "src" / "clankops" / "__init__.py").is_file()


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
