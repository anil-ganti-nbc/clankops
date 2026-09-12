"""Derived resume packets. Read-only. Writes zero ledger events.

Not an LLM summary. Unknown stays unknown. Missing evidence is not advice.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from clankops.attention import attention_report
from clankops.ci import CI_ARTIFACT_KIND
from clankops.deployment import current_deployments
from clankops.errors import NotFoundError
from clankops.readmodel import DEFAULT_STALE, open_sessions
from clankops.reconcile import github_repo_for_clank, reconcile_clank
from clankops.store import Store
from clankops.timefmt import short_head

STATUS_RESUMABLE = "RESUMABLE"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_NO_UNFINISHED_MISSION = "NO_UNFINISHED_MISSION"

_EPHEMERAL_KEYS = frozenset(
    {
        "age",
        "age_seconds",
        "checkpoint_age",
        "ci_capture_age",
        "open_session_age",
    }
)
_FINGERPRINT_SKIP_KEYS = frozenset(
    {
        "context_fingerprint",
        "requested_actor",
        "wrote_events",
    }
)


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json") or row.get("metadata") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _latest_ci(store: Store, mission_id: str) -> dict[str, Any] | None:
    row = store.conn.execute(
        """
        SELECT * FROM artifacts
        WHERE mission_id = ? AND kind = ?
        ORDER BY created_utc DESC, artifact_id DESC
        LIMIT 1
        """,
        (mission_id, CI_ARTIFACT_KIND),
    ).fetchone()
    return dict(row) if row else None


def _ci_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    meta = _meta(row)
    return {
        "artifact_id": row.get("artifact_id"),
        "kind": row.get("kind"),
        "ref": row.get("ref"),
        "sha": meta.get("sha") or row.get("ref"),
        "state": meta.get("state"),
        "created_utc": row.get("created_utc"),
        "source": row.get("source"),
        "title": row.get("title"),
    }


def _checkpoint_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    next_action = row.get("next_action")
    if next_action is not None:
        next_action = str(next_action).strip() or None
    return {
        "checkpoint_id": row.get("checkpoint_id"),
        "recorded_utc": row.get("recorded_utc"),
        "next_action": next_action,
        "completed": row.get("completed"),
        "current_work": row.get("current_work"),
        "branch": row.get("branch"),
        "head": row.get("head"),
        "working_tree": row.get("working_tree"),
        "tests": row.get("tests"),
        "notes": row.get("notes"),
    }


def _strip_for_fingerprint(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_for_fingerprint(item)
            for key, item in value.items()
            if key not in _EPHEMERAL_KEYS and key not in _FINGERPRINT_SKIP_KEYS
        }
    if isinstance(value, list):
        return [_strip_for_fingerprint(item) for item in value]
    return value


def context_fingerprint(packet: dict[str, Any]) -> str:
    """Hash ClankOps facts in the packet. Ages and actor metadata are not facts."""
    facts = _strip_for_fingerprint(packet)
    blob = json.dumps(
        facts, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _admission(unfinished: list[dict[str, Any]]) -> dict[str, Any]:
    if not unfinished:
        return {
            "status": STATUS_NO_UNFINISHED_MISSION,
            "resumable_mission": None,
            "resumable_mission_id": None,
        }
    if len(unfinished) > 1:
        return {
            "status": STATUS_AMBIGUOUS,
            "resumable_mission": None,
            "resumable_mission_id": None,
        }
    row = unfinished[0]
    return {
        "status": STATUS_RESUMABLE,
        "resumable_mission": row["display_id"],
        "resumable_mission_id": row["mission_id"],
    }


def _mission_rows(
    store: Store,
    unfinished: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for mission in unfinished:
        checkpoint = store.latest_checkpoint(mission["clank_id"], mission["mission_id"])
        view = _checkpoint_view(checkpoint)
        blockers = [
            {
                "blocker_id": row["blocker_id"],
                "description": row["description"],
                "state": row["state"],
                "created_utc": row["created_utc"],
            }
            for row in store.conn.execute(
                """
                SELECT * FROM blockers
                WHERE mission_id = ? AND state = 'OPEN'
                ORDER BY created_utc, blocker_id
                """,
                (mission["mission_id"],),
            )
        ]
        tasks = [
            {
                "task_id": row["task_id"],
                "title": row["title"],
                "state": row["state"],
                "created_utc": row["created_utc"],
            }
            for row in store.conn.execute(
                """
                SELECT * FROM tasks
                WHERE mission_id = ? AND state NOT IN ('DONE', 'CANCELLED')
                ORDER BY created_utc, task_id
                """,
                (mission["mission_id"],),
            )
        ]
        decisions = [
            {
                "decision_id": row["decision_id"],
                "statement": row["statement"],
                "rationale": row["rationale"],
                "created_utc": row["created_utc"],
            }
            for row in store.conn.execute(
                """
                SELECT * FROM decisions
                WHERE mission_id = ?
                ORDER BY created_utc DESC, decision_id DESC
                LIMIT 5
                """,
                (mission["mission_id"],),
            )
        ]
        rows.append(
            {
                "mission": mission["display_id"],
                "mission_id": mission["mission_id"],
                "state": mission["state"],
                "objective": mission["objective"],
                "created_utc": mission.get("created_utc"),
                "updated_utc": mission.get("updated_utc"),
                "checkpoint": view,
                "next_action": view["next_action"] if view else None,
                "ci": _ci_view(_latest_ci(store, mission["mission_id"])),
                "blockers": blockers,
                "tasks": tasks,
                "decisions": decisions,
            }
        )
    return rows


def _session_rows(open_for: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "session_id": row.get("session_id"),
            "actor": row.get("actor"),
            "mission_id": row.get("mission_id"),
            "mission_display": row.get("mission_display"),
            "started_utc": row.get("started_utc"),
            "age": row.get("age"),
            "age_seconds": row.get("age_seconds"),
        }
        for row in open_for
    ]
    rows.sort(key=lambda row: (row.get("started_utc") or "", row.get("session_id") or ""))
    return rows


def _deployment_rows(store: Store, clank_id: str, now: datetime) -> list[dict[str, Any]]:
    rows = []
    for row in current_deployments(store, clank_id, now=now):
        mission_id = row.get("mission_id")
        display = None
        if mission_id:
            try:
                display = store.resolve_mission(mission_id)["display_id"]
            except NotFoundError:
                display = None
        rows.append(
            {
                "surface_id": row.get("surface_id"),
                "observation_id": row.get("observation_id"),
                "mission_id": mission_id,
                "mission": display,
                "environment": row.get("environment"),
                "host_identity": row.get("host_identity"),
                "deployed_sha": row.get("deployed_sha"),
                "sha_short": row.get("sha_short") or short_head(row.get("deployed_sha")),
                "deployed": row.get("deployed"),
                "running": row.get("running"),
                "observed_at": row.get("observed_at") or row.get("created_utc"),
                "age": row.get("age"),
                "source": row.get("source"),
            }
        )
    rows.sort(key=lambda row: (row.get("surface_id") or "", row.get("observation_id") or ""))
    return rows


def _observation(rec: dict[str, Any]) -> dict[str, Any]:
    local = rec.get("observed_local") or {}
    ok = bool(local.get("ok"))
    return {
        "ok": ok,
        "error": local.get("error"),
        "path": local.get("path"),
        "branch": local.get("branch") if ok else None,
        "head": local.get("head") if ok else None,
        "working_tree": local.get("working_tree") if ok else None,
        "source": local.get("source"),
    }


def _reconcile_view(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": rec.get("status"),
        "drift": rec.get("drift") or [],
        "mission_display": rec.get("mission_display"),
        "head_short_recorded": rec.get("head_short_recorded"),
        "head_short_local": rec.get("head_short_local"),
    }


def resume_packet(
    store: Store,
    clank: str,
    *,
    now: datetime | None = None,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
) -> dict[str, Any]:
    """Canonical resume packet. Never mutates the ledger."""
    instant = now or store.clock.now()
    detail = store.clank_detail(clank)
    clank_id = detail["clank_id"]
    unfinished = store.unfinished_missions(clank_id)
    rec = reconcile_clank(
        store,
        detail["slug"],
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    attention = attention_report(
        store,
        detail["slug"],
        now=instant,
        stale_after=DEFAULT_STALE,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    open_for = [
        row
        for row in open_sessions(store, now=instant, stale_after=DEFAULT_STALE)
        if row.get("clank_id") == clank_id
    ]
    github = github_repo_for_clank(store, clank_id)
    packet = {
        "derived": True,
        "authoritative": False,
        "wrote_events": False,
        "identity": {
            "clank_id": clank_id,
            "slug": detail["slug"],
            "display_name": detail["display_name"],
            "lifecycle": detail.get("lifecycle"),
            "local_path": store.canonical_local_path(clank_id),
            "github_repo": github.get("repo"),
            "github_rule": github.get("rule"),
            "github_error": github.get("error"),
            "github_ambiguous": github.get("ambiguous"),
        },
        "admission": _admission(unfinished),
        "unfinished_missions": _mission_rows(store, unfinished),
        "open_sessions": _session_rows(open_for),
        "attention": attention.get("items") or [],
        "reconcile": _reconcile_view(rec),
        "observation": _observation(rec),
        "deployments": _deployment_rows(store, clank_id, instant),
    }
    packet["context_fingerprint"] = context_fingerprint(packet)
    return packet


def format_resume_text(packet: dict[str, Any]) -> str:
    ident = packet.get("identity") or {}
    name = ident.get("display_name") or ident.get("slug") or "unknown"
    admission = packet.get("admission") or {}
    lines = [
        str(name),
        f"Admission: {admission.get('status') or 'unknown'}",
    ]
    missions = packet.get("unfinished_missions") or []
    if admission.get("status") == STATUS_RESUMABLE and missions:
        row = missions[0]
        lines.append(f"Mission: {row.get('mission')} {row.get('state')}")
        nxt = row.get("next_action")
        lines.append(f"Next: {nxt if nxt else 'unknown'}")
    elif missions:
        listed = "; ".join(f"{m.get('mission')} {m.get('state')}" for m in missions)
        lines.append(f"Unfinished: {listed}")
        lines.append("Next: unknown")
    else:
        lines.append("Mission: none")
        lines.append("Next: unknown")
    rec = packet.get("reconcile") or {}
    lines.append(f"Git: {rec.get('status') or 'unknown'}")
    obs = packet.get("observation") or {}
    if obs.get("ok"):
        lines.append(
            f"Observed: {obs.get('branch') or 'unknown'}/"
            f"{short_head(obs.get('head')) or 'unknown'} "
            f"{obs.get('working_tree') or 'unknown'}"
        )
    else:
        err = obs.get("error")
        suffix = f" ({err})" if err else ""
        lines.append(f"Observed: unknown{suffix}")
    lines.append("CI:")
    if not missions:
        lines.append("  none recorded")
    else:
        for row in missions:
            ci = row.get("ci")
            if not ci:
                lines.append(f"  {row.get('mission')} none recorded")
            else:
                lines.append(
                    f"  {row.get('mission')} sha={short_head(ci.get('sha')) or 'unknown'} "
                    f"state={ci.get('state') or 'unknown'}"
                )
    lines.append("Deployments:")
    deployments = packet.get("deployments") or []
    if not deployments:
        lines.append("  none recorded")
    else:
        for row in deployments:
            mission = row.get("mission") or "unattributable"
            lines.append(
                f"  {row.get('surface_id') or 'unknown'} "
                f"sha={row.get('sha_short') or 'unknown'} "
                f"mission={mission}"
            )
    lines.append("Attention:")
    items = packet.get("attention") or []
    if not items:
        lines.append("  none derived")
    else:
        for item in items:
            mission = item.get("mission") or ""
            extra = f" {mission}" if mission else ""
            lines.append(f"  {item.get('reason_code')}{extra} {item.get('reason')}")
    lines.append("Open Sessions:")
    sessions = packet.get("open_sessions") or []
    if not sessions:
        lines.append("  none")
    else:
        for row in sessions:
            lines.append(
                f"  {row.get('actor') or 'unknown'} "
                f"{row.get('session_id')} "
                f"mission={row.get('mission_display') or 'unknown'} "
                f"age={row.get('age') or 'unknown'}"
            )
    lines.append(f"Context: {packet.get('context_fingerprint')}")
    return "\n".join(lines) + "\n"
