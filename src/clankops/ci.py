"""Attach observed GitHub CI to a Mission as artefacts.

Reconcile stays read-only. This module is the explicit write path.
It does not rewrite checkpoints or git claims.
"""

from __future__ import annotations

from typing import Any

from clankops.enums import EventSource, MissionState
from clankops.errors import ValidationError
from clankops.reconcile import reconcile_clank
from clankops.store import Store

CI_ARTIFACT_KIND = "github_ci"
UNFINISHED_MISSION_STATES = frozenset(
    {
        MissionState.PLANNED,
        MissionState.ACTIVE,
        MissionState.PAUSED,
        MissionState.BLOCKED,
    }
)
_FAILED = {"failure", "cancelled", "timed_out", "action_required", "error"}
_TITLE_FAILING_LIMIT = 5
_TITLE_FAILING_CHARS = 80


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url"),
    }


def _context_summary(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "context": ctx.get("context"),
        "state": ctx.get("state"),
        "target_url": ctx.get("target_url"),
        "description": ctx.get("description"),
    }


def _failing_run_names(runs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for run in runs:
        if str(run.get("conclusion") or "").lower() in _FAILED:
            names.append(str(run.get("name") or "check"))
    return names


def _failing_context_names(contexts: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for ctx in contexts:
        if str(ctx.get("state") or "").lower() in _FAILED:
            names.append(str(ctx.get("context") or "status"))
    return names


def _title_failing_suffix(names: list[str]) -> str:
    if not names:
        return ""
    shown = names[:_TITLE_FAILING_LIMIT]
    text = ", ".join(shown)
    extra = len(names) - len(shown)
    if extra:
        text += f" (+{extra})"
    if len(text) > _TITLE_FAILING_CHARS:
        text = text[: _TITLE_FAILING_CHARS - 3].rstrip(", ") + "..."
    return " failing: " + text


def _observation_failure_reason(github: dict[str, Any], checks: dict[str, Any]) -> str:
    if not (github.get("repo") or checks.get("repo")):
        return github.get("error") or "no GitHub repo"
    reason = checks.get("error") or github.get("error") or "nothing was observed"
    if reason == "not requested":
        return "no CI target"
    return str(reason)


def _resolve_capture_mission(
    store: Store,
    clank: str,
    clank_id: str,
    mission: str | None,
) -> dict[str, Any]:
    """Attach only to an unfinished Mission. Never use active_or_unfinished_mission()."""
    if mission:
        row = store.resolve_mission(mission)
        if row["clank_id"] != clank_id:
            owner = store.clank_detail(row["clank_id"])
            raise ValidationError(
                f"mission {row['display_id']} belongs to "
                f"{owner.get('slug') or row['clank_id']}, not {clank}"
            )
        if row["state"] not in UNFINISHED_MISSION_STATES:
            raise ValidationError(
                f"mission {row['display_id']} is {row['state']}; "
                "CI artefacts attach only to unfinished Missions "
                "(PLANNED / ACTIVE / PAUSED / BLOCKED)"
            )
        return row
    unfinished = store.unfinished_missions(clank)
    if not unfinished:
        raise ValidationError(f"no unfinished Mission for {clank} to attach CI artefact")
    if len(unfinished) > 1:
        listed = ", ".join(f"{m['display_id']} [{m['state']}]" for m in unfinished)
        raise ValidationError(
            f"multiple unfinished Missions for {clank}: {listed}. "
            "Pass --mission COPS-xxxxxx"
        )
    return unfinished[0]


def capture_ci(
    store: Store,
    clank: str,
    *,
    mission: str | None = None,
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
    github = rec.get("observed_github") or {}
    checks = github.get("checks") or {}
    if checks.get("state") is None:
        reason = _observation_failure_reason(github, checks)
        raise ValidationError(
            f"no GitHub CI observation to record ({reason}); "
            "a local SHA is not a CI observation"
        )
    target = _resolve_capture_mission(store, clank, rec["clank_id"], mission)
    runs = [_run_summary(run) for run in (checks.get("runs") or [])]
    contexts = [_context_summary(ctx) for ctx in (checks.get("contexts") or [])]
    failing_runs = _failing_run_names(runs)
    failing_contexts = _failing_context_names(contexts)
    state = checks.get("state")
    sha = checks.get("sha")
    title = f"CI {state} {(sha or '')[:7]}"
    title += _title_failing_suffix(failing_runs + failing_contexts)
    row = store.attach_artifact(
        mission=target["display_id"],
        kind=CI_ARTIFACT_KIND,
        ref=str(sha or "unknown"),
        title=title,
        source=EventSource.CI,
        artifact_source=EventSource.CI,
        metadata={
            "repo": checks.get("repo") or github.get("repo"),
            "sha": sha,
            "state": state,
            "error": checks.get("error"),
            "checks_observed": checks.get("checks_observed"),
            "statuses_observed": checks.get("statuses_observed"),
            "check_run_total": checks.get("check_run_total"),
            "checks_complete": checks.get("checks_complete"),
            "status_total": checks.get("status_total"),
            "statuses_complete": checks.get("statuses_complete"),
            "combined_state": checks.get("combined_state"),
            "runs": runs,
            "contexts": contexts,
            "failing_runs": failing_runs,
            "failing_contexts": failing_contexts,
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
        "mission_display": target["display_id"],
    }
