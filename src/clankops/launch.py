"""Managed agent launch gate. Prepare, admit, then spawn. Fail closed.

Does not invent Missions, handoffs, or next actions. Child exit is not a
Mission completion. Actor and launcher names are provenance, not permission.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from clankops.agent import admit_agent, prepare_agent, require_actor
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
