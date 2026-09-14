"""One-shot local Git fleet harvest.

Observes registered canonical checkouts. Does not interpret work, fetch,
or promote LOCAL_GIT into GitHub/CI/deployment/Mission state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from clankops.clock import isoformat_utc
from clankops.enums import EventSource, EventType
from clankops.gitinspect import (
    GIT_TIMEOUT_SEC,
    RESULT_ERROR,
    RESULT_NOT_A_GIT_REPOSITORY,
    RESULT_OBSERVED,
    RESULT_PATH_MISSING,
    RESULT_TIMEOUT,
    _bounded_detail,
    git_executable_available,
    observe_local_git_checkout,
)
from clankops.ids import new_id
from clankops.redact import sanitize_captured, sanitize_text
from clankops.store import Store
from clankops.timefmt import format_age, parse_utc, short_head

RECORDER = "clankops.harvest"
RESULT_OBSERVED_CHANGED = "OBSERVED_CHANGED"
RESULT_OBSERVED_UNCHANGED = "OBSERVED_UNCHANGED"
RESULT_NO_CANONICAL_PATH = "NO_CANONICAL_PATH"
RESULT_AMBIGUOUS_CANONICAL_PATH = "AMBIGUOUS_CANONICAL_PATH"

HARD_ERRORS = frozenset(
    {
        RESULT_TIMEOUT,
        RESULT_ERROR,
        RESULT_AMBIGUOUS_CANONICAL_PATH,
        "GIT_MISSING",
    }
)
UNAVAILABLE = frozenset({RESULT_PATH_MISSING, RESULT_NOT_A_GIT_REPOSITORY})
SKIPPED = frozenset({RESULT_NO_CANONICAL_PATH})
FAILURE_EVIDENCE = frozenset(
    {
        RESULT_TIMEOUT,
        RESULT_ERROR,
        RESULT_PATH_MISSING,
        RESULT_NOT_A_GIT_REPOSITORY,
        RESULT_AMBIGUOUS_CANONICAL_PATH,
        "GIT_MISSING",
        "STALE_OBSERVATION",
    }
)
WRITE_CHANGED = "changed"
WRITE_UNCHANGED = "unchanged"
WRITE_STALE = "stale"

ObserveFn = Callable[..., dict[str, Any]]


def checkout_key_for_path(path: str | Path) -> str:
    """Stable checkout identity from the canonical registered path.

    A later move of local_path is a new checkout identity. Historical
    observations are not rewritten onto the new path.
    """
    digest = hashlib.sha256(_normalize_checkout_path(path).encode("utf-8")).hexdigest()
    return f"local_path:{digest}"


def _normalize_checkout_path(path: str | Path) -> str:
    return Store._normalize_fs_path(path)


def _canonical_local_path_refs(detail: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        ref
        for ref in (detail.get("refs") or [])
        if ref.get("ref_kind") == "local_path" and ref.get("is_canonical")
    ]
    return refs


def _noncanonical_local_path_refs(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ref
        for ref in (detail.get("refs") or [])
        if ref.get("ref_kind") == "local_path" and not ref.get("is_canonical")
    ]


def _alternate_checkouts(detail: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ref in _noncanonical_local_path_refs(detail):
        rows.append(
            {
                "path": ref.get("ref_value"),
                "canonical": False,
            }
        )
    rows.sort(key=lambda row: str(row.get("path") or ""))
    return rows


def semantic_state_payload(
    *,
    clank_id: str,
    checkout_key: str,
    checkout_path: str,
    observed: dict[str, Any],
) -> dict[str, Any]:
    remotes = sorted(
        list(observed.get("remotes") or []),
        key=lambda row: (str(row.get("name") or ""), str(row.get("identity") or "")),
    )
    worktrees = list(observed.get("worktrees") or [])
    return {
        "clank_id": clank_id,
        "checkout_key": checkout_key,
        "checkout_path": checkout_path,
        "branch": observed.get("branch"),
        "detached": observed.get("detached"),
        "head": observed.get("head"),
        "dirty": observed.get("dirty"),
        "dirty_count": observed.get("dirty_count"),
        "tracked_changes": observed.get("tracked_changes"),
        "untracked": observed.get("untracked"),
        "upstream": observed.get("upstream"),
        "upstream_ahead_local": observed.get("upstream_ahead_local"),
        "upstream_behind_local": observed.get("upstream_behind_local"),
        "remotes": remotes,
        "worktrees": worktrees,
        "upstream_freshness": "against local tracking ref; remote freshness unknown",
    }


def state_fingerprint(payload: dict[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _latest_observation(store: Store, clank_id: str, checkout_key: str) -> dict[str, Any] | None:
    row = store.conn.execute(
        """
        SELECT * FROM local_git_observations
        WHERE clank_id = ? AND checkout_key = ?
        ORDER BY ledger_seq DESC, observation_id DESC
        LIMIT 1
        """,
        (clank_id, checkout_key),
    ).fetchone()
    return dict(row) if row else None


def _latest_result(store: Store, clank_id: str) -> dict[str, Any] | None:
    row = store.conn.execute(
        """
        SELECT r.*, h.finished_at, h.run_id AS harvest_run_id, h.ledger_seq AS run_ledger_seq
        FROM local_git_harvest_results r
        JOIN local_git_harvest_runs h ON h.run_id = r.run_id
        WHERE r.clank_id = ?
        ORDER BY h.ledger_seq DESC, r.result_id DESC
        LIMIT 1
        """,
        (clank_id,),
    ).fetchone()
    return dict(row) if row else None


def _select_targets(store: Store, clank: str | None) -> list[dict[str, Any]]:
    if clank:
        return [store.clank_detail(clank)]
    return [store.clank_detail(row["clank_id"]) for row in store.list_clanks()]


def _classify_target(detail: dict[str, Any]) -> dict[str, Any]:
    clank_id = detail["clank_id"]
    slug = detail["slug"]
    canon = _canonical_local_path_refs(detail)
    alternates = _alternate_checkouts(detail)
    if not canon:
        return {
            "clank_id": clank_id,
            "slug": slug,
            "result_code": RESULT_NO_CANONICAL_PATH,
            "checkout_key": None,
            "checkout_path": None,
            "alternate_checkouts": alternates,
            "detail": "no canonical local_path",
        }
    if len(canon) > 1:
        return {
            "clank_id": clank_id,
            "slug": slug,
            "result_code": RESULT_AMBIGUOUS_CANONICAL_PATH,
            "checkout_key": None,
            "checkout_path": None,
            "alternate_checkouts": alternates,
            "detail": "ambiguous canonical local_path; refusing to guess",
        }
    path = str(canon[0]["ref_value"])
    return {
        "clank_id": clank_id,
        "slug": slug,
        "result_code": None,
        "checkout_key": checkout_key_for_path(path),
        "checkout_path": path,
        "alternate_checkouts": alternates,
        "detail": None,
    }


def _observation_generation(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    return int(row.get("ledger_seq") or 0)


def _record_state_observation(
    store: Store,
    *,
    actor: str,
    payload: dict[str, Any],
    fingerprint: str,
    observed_at: str,
    expected_generation: int,
) -> tuple[str, Any]:
    """Lock, re-read generation/fingerprint, maybe append. Never write stale state."""
    clank_id = payload["clank_id"]
    checkout_key = payload["checkout_key"]
    store._begin_immediate()
    try:
        latest = _latest_observation(store, clank_id, checkout_key)
        generation = _observation_generation(latest)
        if latest and latest.get("state_fingerprint") == fingerprint:
            store.commit()
            return WRITE_UNCHANGED, None
        if generation != expected_generation:
            store.commit()
            return WRITE_STALE, None
        observation_id = new_id()
        event = store._emit(
            EventType.LOCAL_GIT_STATE_OBSERVED,
            {
                **payload,
                "observation_id": observation_id,
                "state_fingerprint": fingerprint,
                "observed_at": observed_at,
            },
            actor=actor,
            source=EventSource.LOCAL_GIT,
            clank_id=clank_id,
            mission_id=None,
            session_id=None,
            provenance={"recorder": RECORDER, "source_authority": EventSource.LOCAL_GIT},
            bind_session=False,
        )
        store.commit()
        return WRITE_CHANGED, event
    except Exception:
        store.conn.rollback()
        raise


def harvest_local_git(
    store: Store,
    clank: str | None = None,
    *,
    dry_run: bool = False,
    actor: str | None = None,
    observe: ObserveFn | None = None,
    timeout: float | int | None = GIT_TIMEOUT_SEC,
    git_available: bool | None = None,
) -> dict[str, Any]:
    """Walk registered Clanks and record local Git evidence.

    Dry-run inspects and reports but writes zero events or projections.
    """
    observer = (actor or store.default_actor or "").strip() or "harvest"
    run_id = new_id()
    started_at = isoformat_utc(store.clock.now())
    observe_fn = observe or observe_local_git_checkout
    available = git_executable_available(timeout=timeout) if git_available is None else git_available
    targets = _select_targets(store, clank)
    results: list[dict[str, Any]] = []
    changed = 0
    unchanged = 0
    skipped = 0
    unavailable = 0
    errors = 0

    for detail in targets:
        classified = _classify_target(detail)
        result_code = classified["result_code"]
        row: dict[str, Any] = {
            "clank_id": classified["clank_id"],
            "slug": classified["slug"],
            "checkout_key": classified["checkout_key"],
            "checkout_path": classified["checkout_path"],
            "alternate_checkouts": classified["alternate_checkouts"],
            "result": result_code,
            "detail": classified["detail"],
            "observation_event_id": None,
            "observation_ledger_seq": None,
            "state_fingerprint": None,
            "state": None,
            "changed": False,
        }
        if result_code == RESULT_NO_CANONICAL_PATH:
            skipped += 1
            results.append(row)
            continue
        if result_code == RESULT_AMBIGUOUS_CANONICAL_PATH:
            errors += 1
            results.append(row)
            continue
        if not available:
            row["result"] = RESULT_ERROR
            row["detail"] = "git executable not found"
            errors += 1
            results.append(row)
            continue
        expected_generation = _observation_generation(
            _latest_observation(store, classified["clank_id"], classified["checkout_key"])
        )
        if store.conn.in_transaction:
            store.conn.commit()
        try:
            observed = observe_fn(classified["checkout_path"], timeout=timeout)
            if not observed.get("ok"):
                code = str(observed.get("result_code") or RESULT_ERROR)
                row["result"] = code
                row["detail"] = observed.get("detail")
                if code in UNAVAILABLE:
                    unavailable += 1
                else:
                    errors += 1
                results.append(row)
                continue
            semantic = semantic_state_payload(
                clank_id=classified["clank_id"],
                checkout_key=classified["checkout_key"],
                checkout_path=_normalize_checkout_path(classified["checkout_path"]),
                observed=observed["state"],
            )
            fingerprint = state_fingerprint(semantic)
        except Exception as exc:
            row["result"] = RESULT_ERROR
            row["detail"] = _bounded_detail(f"{exc.__class__.__name__}: {exc}")
            errors += 1
            results.append(row)
            continue
        row["state"] = semantic
        row["state_fingerprint"] = fingerprint
        observed_at = isoformat_utc(store.clock.now())
        if dry_run:
            latest = _latest_observation(store, classified["clank_id"], classified["checkout_key"])
            would_change = not latest or latest.get("state_fingerprint") != fingerprint
            row["changed"] = would_change
            row["result"] = RESULT_OBSERVED_CHANGED if would_change else RESULT_OBSERVED_UNCHANGED
        else:
            outcome, event = _record_state_observation(
                store,
                actor=observer,
                payload=semantic,
                fingerprint=fingerprint,
                observed_at=observed_at,
                expected_generation=expected_generation,
            )
            if outcome == WRITE_STALE:
                row["result"] = RESULT_ERROR
                row["detail"] = "stale local git observation discarded"
                errors += 1
                results.append(row)
                continue
            row["changed"] = outcome == WRITE_CHANGED
            row["result"] = RESULT_OBSERVED_CHANGED if row["changed"] else RESULT_OBSERVED_UNCHANGED
            if event is not None:
                row["observation_event_id"] = event.event_id
                row["observation_ledger_seq"] = event.ledger_seq
            elif outcome == WRITE_UNCHANGED:
                latest = _latest_observation(
                    store, classified["clank_id"], classified["checkout_key"]
                )
                if latest:
                    row["observation_event_id"] = latest.get("event_id")
                    row["observation_ledger_seq"] = latest.get("ledger_seq")
        if row["result"] == RESULT_OBSERVED_CHANGED:
            changed += 1
        else:
            unchanged += 1
        results.append(row)

    finished_at = isoformat_utc(store.clock.now())
    summary = {
        "run_id": run_id,
        "scope": "targeted" if clank else "fleet",
        "target": clank,
        "dry_run": dry_run,
        "started_at": started_at,
        "finished_at": finished_at,
        "target_count": len(targets),
        "observed_changed": changed,
        "observed_unchanged": unchanged,
        "unavailable": unavailable,
        "skipped": skipped,
        "errors": errors,
        "wrote_events": False,
        "source_authority": {
            "observation": EventSource.LOCAL_GIT,
            "harvest_run": EventSource.SYSTEM,
        },
    }
    if not dry_run:
        bounded_results = [_persistable_result(item) for item in results]
        store._begin_immediate()
        try:
            event = store._emit(
                EventType.LOCAL_GIT_HARVEST_COMPLETED,
                {
                    "run_id": run_id,
                    "scope": summary["scope"],
                    "target": clank,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "target_count": len(targets),
                    "observed_changed": changed,
                    "observed_unchanged": unchanged,
                    "unavailable": unavailable,
                    "skipped": skipped,
                    "errors": errors,
                    "results": bounded_results,
                },
                actor=observer,
                source=EventSource.SYSTEM,
                clank_id=None if summary["scope"] == "fleet" else results[0]["clank_id"] if results else None,
                mission_id=None,
                session_id=None,
                provenance={"recorder": RECORDER, "source_authority": EventSource.SYSTEM},
                bind_session=False,
            )
            store.commit()
            summary["wrote_events"] = True
            summary["event_id"] = event.event_id
            summary["ledger_seq"] = event.ledger_seq
        except Exception:
            store.conn.rollback()
            raise
    payload = sanitize_captured({**summary, "results": results})
    return payload


def _persistable_result(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("state") or {}
    return {
        "result_id": new_id(),
        "clank_id": row.get("clank_id"),
        "clank_slug": row.get("slug"),
        "checkout_key": row.get("checkout_key"),
        "checkout_path": row.get("checkout_path"),
        "result_code": row.get("result"),
        "observation_event_id": row.get("observation_event_id"),
        "observation_ledger_seq": row.get("observation_ledger_seq"),
        "state_fingerprint": row.get("state_fingerprint"),
        "error_class": row.get("result") if row.get("result") in HARD_ERRORS | UNAVAILABLE | SKIPPED else None,
        "detail": sanitize_text(row.get("detail")) if row.get("detail") else None,
        "branch": state.get("branch"),
        "detached": state.get("detached"),
        "head": state.get("head"),
        "dirty": state.get("dirty"),
        "dirty_count": state.get("dirty_count"),
    }


def harvest_exit_code(payload: dict[str, Any]) -> int:
    """0 = completed, no hard errors. 1 = completed with hard errors. 2 = control-plane."""
    if int(payload.get("errors") or 0) > 0:
        return 1
    return 0


def format_harvest_text(payload: dict[str, Any]) -> str:
    lines = [
        "Local Git Harvest",
        f"targets: {payload.get('target_count') or 0}",
        f"changed: {payload.get('observed_changed') or 0}",
        f"unchanged: {payload.get('observed_unchanged') or 0}",
        f"skipped: {payload.get('skipped') or 0}",
        f"errors: {payload.get('errors') or 0}",
        "",
    ]
    if payload.get("dry_run"):
        lines.insert(1, "mode: dry-run (zero writes)")
    for row in payload.get("results") or []:
        result = str(row.get("result") or "ERROR")
        slug = str(row.get("slug") or "unknown")
        state = row.get("state") or {}
        if result in {RESULT_OBSERVED_CHANGED, RESULT_OBSERVED_UNCHANGED}:
            branch = state.get("branch") or ("HEAD" if state.get("detached") else "unknown")
            head = short_head(state.get("head")) or "unknown"
            if state.get("dirty"):
                tree = f"DIRTY({state.get('dirty_count') or 0})"
            elif state.get("dirty") is False:
                tree = "CLEAN"
            else:
                tree = "UNKNOWN"
            label = "CHANGED" if result == RESULT_OBSERVED_CHANGED else "UNCHANGED"
            lines.append(f"{label:<10} {slug:<16} {branch:<22} {head} {tree}")
        elif result == RESULT_PATH_MISSING:
            lines.append(f"{'MISSING':<10} {slug:<16} canonical path unavailable")
        elif result == RESULT_NO_CANONICAL_PATH:
            lines.append(f"{'SKIPPED':<10} {slug:<16} no canonical local_path")
        elif result == RESULT_AMBIGUOUS_CANONICAL_PATH:
            lines.append(f"{'AMBIGUOUS':<10} {slug:<16} canonical path ambiguous")
        elif result == RESULT_NOT_A_GIT_REPOSITORY:
            lines.append(f"{'NOTGIT':<10} {slug:<16} not a git repository")
        elif result == RESULT_TIMEOUT:
            lines.append(f"{'ERROR':<10} {slug:<16} git inspection timed out")
        else:
            detail = row.get("detail") or "git inspection failed"
            lines.append(f"{'ERROR':<10} {slug:<16} {detail}")
    return "\n".join(lines) + "\n"


def local_git_harvest_view(
    store: Store,
    clank: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Latest harvested local Git evidence. Does not overwrite reconcile."""
    detail = store.clank_detail(clank)
    clank_id = detail["clank_id"]
    classified = _classify_target(detail)
    checkout_key = classified.get("checkout_key")
    observation = (
        _latest_observation(store, clank_id, checkout_key) if checkout_key else None
    )
    latest = _latest_result(store, clank_id)
    instant = now or store.clock.now()
    state = None
    if observation and observation.get("state_json"):
        try:
            state = json.loads(observation["state_json"])
        except json.JSONDecodeError:
            state = None
    observed_at = observation.get("observed_at") if observation else None
    last_checked = latest.get("finished_at") if latest else None
    check_delta = None
    if last_checked:
        parsed = parse_utc(last_checked)
        if parsed is not None:
            check_delta = instant - parsed
    state_age = None
    if observed_at:
        parsed = parse_utc(observed_at)
        if parsed is not None:
            state_age = instant - parsed
    return {
        "clank_id": clank_id,
        "slug": detail["slug"],
        "checkout_key": checkout_key or (observation.get("checkout_key") if observation else None),
        "checkout_path": classified.get("checkout_path")
        or (observation.get("checkout_path") if observation else None),
        "alternate_checkouts": classified.get("alternate_checkouts") or [],
        "canonical_selection": classified.get("result_code"),
        "semantic_state": state,
        "state_fingerprint": observation.get("state_fingerprint") if observation else None,
        "state_observed_at": observed_at,
        "state_observation_age": format_age(state_age),
        "last_checked_at": last_checked,
        "check_age": format_age(check_delta),
        "check_age_seconds": int(check_delta.total_seconds()) if check_delta is not None else None,
        "latest_result": latest.get("result_code") if latest else None,
        "latest_result_detail": latest.get("detail") if latest else None,
        "observation_event_id": observation.get("event_id") if observation else None,
        "observation_ledger_seq": observation.get("ledger_seq") if observation else None,
        "harvest_run_id": latest.get("harvest_run_id") if latest else None,
        "source": EventSource.LOCAL_GIT if observation else None,
        "never_harvested": observation is None,
    }


def local_git_harvest_facts(view: dict[str, Any]) -> dict[str, Any]:
    """Resume-packet facts. Successful unchanged freshness is ephemeral.

    Failed/unavailable harvest outcomes are evidence and are hashed as
    `latest_failure`. Repeated identical failure class need not churn.
    """
    state = view.get("semantic_state")
    result = view.get("latest_result")
    failure = result if result in FAILURE_EVIDENCE else None
    return {
        "checkout_key": view.get("checkout_key"),
        "checkout_path": view.get("checkout_path"),
        "canonical_selection": view.get("canonical_selection"),
        "semantic_state": state,
        "state_fingerprint": view.get("state_fingerprint"),
        "state_observed_at": view.get("state_observed_at"),
        "observation_event_id": view.get("observation_event_id"),
        "observation_ledger_seq": view.get("observation_ledger_seq"),
        "source": view.get("source"),
        "never_harvested": view.get("never_harvested"),
        "last_checked_at": view.get("last_checked_at"),
        "check_age": view.get("check_age"),
        "check_age_seconds": view.get("check_age_seconds"),
        "latest_result": result,
        "latest_failure": failure,
        "latest_result_detail": view.get("latest_result_detail"),
        "harvest_run_id": view.get("harvest_run_id"),
        "alternate_checkouts": view.get("alternate_checkouts") or [],
    }
