"""Explicit capture of deployed/runtime observations.

First-class evidence, not an orchestrator. Reconcile stays read-only.
This slice does not SSH, schedule, or mutate remote hosts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from clankops.capture import resolve_unfinished_capture_mission
from clankops.clock import isoformat_utc
from clankops.enums import EventSource, EventType
from clankops.errors import ValidationError
from clankops.ids import new_id
from clankops.redact import sanitize_captured, sanitize_text
from clankops.store import Store
from clankops.timefmt import format_age, parse_utc, short_head

ENVIRONMENTS = frozenset({"prod", "staging", "canary", "dev", "experimental"})
RUNNING_STATES = frozenset(
    {"unknown", "stopped", "running", "scheduled", "scheduled_oneshot"}
)
TRI_STATES = frozenset({"yes", "no", "unknown"})
SCHEDULERS = frozenset({"unknown", "none", "cron", "dsm_task"})


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: Any, *, field: str, required: bool = False) -> str | None:
    if _blank(value):
        if required:
            raise ValidationError(f"{field} is required")
        return None
    return str(value).strip()


def _closed(value: Any, allowed: frozenset[str], *, field: str, default: str) -> str:
    if _blank(value):
        return default
    text = str(value).strip().lower()
    if text not in allowed:
        raise ValidationError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return text


def _tri(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if _blank(value):
        return "unknown"
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return "yes"
    if text in {"false", "0"}:
        return "no"
    if text not in TRI_STATES:
        raise ValidationError(f"{field} must be yes, no, or unknown")
    return text


def _metadata(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"metadata is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("metadata must be a JSON object")
    cleaned = sanitize_captured(value)
    if not isinstance(cleaned, dict):
        return {}
    return cleaned


def _surface_key(environment: str, host: str) -> str:
    return f"{environment}:{host}"


def _present(
    row: dict[str, Any],
    *,
    is_current: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    meta_raw = row.get("metadata")
    if meta_raw is None:
        meta_raw = row.get("metadata_json") or "{}"
    if isinstance(meta_raw, str):
        metadata = json.loads(meta_raw)
    else:
        metadata = dict(meta_raw)
    observed_at = row.get("observed_at")
    age = None
    if now is not None and observed_at:
        instant = parse_utc(observed_at)
        if instant is not None:
            age = format_age(now - instant)
    sha = row.get("deployed_sha")
    return {
        "observation_id": row["observation_id"],
        "clank_id": row["clank_id"],
        "mission_id": row.get("mission_id"),
        "environment": row["environment"],
        "host_identity": row["host_identity"],
        "surface_key": _surface_key(row["environment"], row["host_identity"]),
        "runtime_path": row.get("runtime_path"),
        "deployed_sha": sha,
        "sha_short": short_head(sha),
        "image_id": row.get("image_id"),
        "runtime_identity": row.get("runtime_identity"),
        "deployed": row.get("deployed") or "unknown",
        "running": row.get("running") or "unknown",
        "scheduler": row.get("scheduler") or "unknown",
        "scheduler_cadence": row.get("scheduler_cadence"),
        "state_store": row.get("state_store"),
        "collection_authority": row.get("collection_authority") or "unknown",
        "notification_authority": row.get("notification_authority") or "unknown",
        "webhook_configured": row.get("webhook_configured") or "unknown",
        "sent_count": row.get("sent_count"),
        "observed_at": observed_at,
        "observed_how": row.get("observed_how"),
        "observer": row.get("observer"),
        "source": row.get("source"),
        "notes": row.get("notes"),
        "metadata": metadata,
        "created_utc": row.get("created_utc"),
        "ledger_seq": row.get("ledger_seq"),
        "age": age,
        "is_current": is_current,
    }


def _rows_for_clank(store: Store, clank: str) -> list[dict[str, Any]]:
    clank_id = store.resolve_clank(clank)["clank_id"]
    rows = store.conn.execute(
        """
        SELECT * FROM deployment_observations
        WHERE clank_id = ?
        ORDER BY ledger_seq ASC
        """,
        (clank_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _current_ids(rows: list[dict[str, Any]]) -> set[str]:
    latest: dict[tuple[str, str], str] = {}
    for row in rows:
        latest[(row["environment"], row["host_identity"])] = row["observation_id"]
    return set(latest.values())


def list_deployments(
    store: Store,
    clank: str,
    *,
    history: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    instant = now or store.clock.now()
    rows = _rows_for_clank(store, clank)
    current_ids = _current_ids(rows)
    presented = [
        _present(row, is_current=row["observation_id"] in current_ids, now=instant)
        for row in rows
    ]
    if history:
        return presented
    return [row for row in presented if row["is_current"]]


def current_deployments(
    store: Store,
    clank: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    return list_deployments(store, clank, history=False, now=now)


def capture_deployment(
    store: Store,
    clank: str,
    *,
    environment: str | None = None,
    host: str | None = None,
    mission: str | None = None,
    runtime_path: str | None = None,
    deployed_sha: str | None = None,
    image: str | None = None,
    runtime_identity: str | None = None,
    deployed: str | bool | None = None,
    running: str | None = None,
    scheduler: str | None = None,
    cadence: str | None = None,
    state_store: str | None = None,
    collection_authority: str | bool | None = None,
    notification_authority: str | bool | None = None,
    webhook_configured: str | bool | None = None,
    sent_count: int | None = None,
    observed_how: str | None = None,
    notes: str | None = None,
    metadata: Any = None,
    observer: str | None = None,
) -> dict[str, Any]:
    """Append an immutable deployment observation. Does not rewrite history."""
    clank_row = store.resolve_clank(clank)
    clank_id = clank_row["clank_id"]
    mission_row = resolve_unfinished_capture_mission(
        store,
        clank,
        clank_id,
        mission,
        attach_kind="deployment observation",
        attach_kind_plural="deployment observations",
    )
    env = _closed(environment, ENVIRONMENTS, field="environment", default="")
    if not env:
        raise ValidationError(
            "environment is required (prod, staging, canary, dev, experimental)"
        )
    host_identity = _text(host, field="host", required=True)
    how = _text(observed_how, field="observed-how", required=True)
    assert host_identity is not None
    assert how is not None
    how = sanitize_text(how) or how
    observer_name = _text(observer, field="observer") or store.default_actor
    notes_clean = sanitize_text(_text(notes, field="notes"))
    meta = _metadata(metadata)
    observation_id = new_id()
    observed_at = isoformat_utc(store.clock.now())
    payload = {
        "observation_id": observation_id,
        "environment": env,
        "host_identity": host_identity,
        "runtime_path": _text(runtime_path, field="runtime-path"),
        "deployed_sha": _text(deployed_sha, field="deployed-sha"),
        "image_id": _text(image, field="image"),
        "runtime_identity": _text(runtime_identity, field="runtime-identity"),
        "deployed": _tri(deployed, field="deployed"),
        "running": _closed(running, RUNNING_STATES, field="running", default="unknown"),
        "scheduler": _closed(
            scheduler, SCHEDULERS, field="scheduler", default="unknown"
        ),
        "scheduler_cadence": _text(cadence, field="cadence"),
        "state_store": _text(state_store, field="state-store"),
        "collection_authority": _tri(
            collection_authority, field="collection-authority"
        ),
        "notification_authority": _tri(
            notification_authority, field="notification-authority"
        ),
        "webhook_configured": _tri(webhook_configured, field="webhook-configured"),
        "sent_count": sent_count,
        "observed_at": observed_at,
        "observed_how": how,
        "observer": observer_name,
        "notes": notes_clean,
        "metadata": meta,
    }
    store._emit(
        EventType.DEPLOYMENT_OBSERVED,
        payload,
        source=EventSource.DEPLOYMENT,
        clank_id=clank_id,
        mission_id=mission_row["mission_id"],
        provenance={
            "recorder": "clankops.deployment",
            "observed_how": how,
            "observer": observer_name,
        },
    )
    store.commit()
    row = dict(
        store.conn.execute(
            "SELECT * FROM deployment_observations WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
    )
    return {
        "observation": _present(row, is_current=True, now=store.clock.now()),
        "mission_display": mission_row["display_id"],
        "rewrote_history": False,
    }
