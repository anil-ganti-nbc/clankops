"""Attach observed GitHub CI to a Mission as artefacts.

Reconcile stays read-only. This module is the explicit write path.
It does not rewrite checkpoints or git claims.
"""

from __future__ import annotations

from typing import Any

from clankops.enums import EventSource
from clankops.errors import ValidationError
from clankops.reconcile import reconcile_clank
from clankops.store import Store

CI_ARTIFACT_KIND = "github_ci"
_FAILED = {"failure", "cancelled", "timed_out", "action_required", "error"}


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url"),
    }


def capture_ci(
    store: Store,
    clank: str,
    *,
    inspect_local=None,
    inspect_remote=None,
) -> dict[str, Any]:
    """Observe CI (same as reconcile) and attach it as a Mission artefact."""
    rec = reconcile_clank(
        store,
        clank,
        include_github=True,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    checks = (rec.get("observed_github") or {}).get("checks") or {}
    sha = (
        checks.get("sha")
        or (rec.get("observed_local") or {}).get("head")
        or (rec.get("recorded") or {}).get("head")
    )
    if not checks and not sha:
        raise ValidationError("no CI observation to record")
    mission = store.active_or_unfinished_mission(rec["clank_id"])
    if not mission:
        raise ValidationError(f"no unfinished Mission for {clank} to attach CI artefact")
    runs = [_run_summary(run) for run in (checks.get("runs") or [])]
    failing = [
        run["name"] or "check"
        for run in runs
        if str(run.get("conclusion") or "").lower() in _FAILED
    ]
    state = checks.get("state")
    title = f"CI {state or 'unknown'} {(sha or '')[:7]}"
    if failing:
        title += " failing: " + ", ".join(failing)
    row = store.attach_artifact(
        mission=mission["display_id"],
        kind=CI_ARTIFACT_KIND,
        ref=str(sha or "unknown"),
        title=title,
        source=EventSource.CI,
        artifact_source=EventSource.CI,
        metadata={
            "state": state,
            "sha": sha,
            "check_run_total": checks.get("check_run_total"),
            "checks_complete": checks.get("checks_complete"),
            "status_total": checks.get("status_total"),
            "statuses_complete": checks.get("statuses_complete"),
            "runs": runs,
            "git_status": rec.get("status"),
        },
    )
    return {
        "artifact": row,
        "ci_state": state,
        "sha": sha,
        "git_status": rec.get("status"),
        "rewrote_history": False,
        "rewrote_git_claims": False,
        "mission_display": mission["display_id"],
    }
