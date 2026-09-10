"""Compare live Git/GitHub observations to recorded checkpoint claims.

Read-only. Never appends events, never updates projections, never fetches
into a product checkout, never closes Sessions, never rewrites history.
"""

from __future__ import annotations

from typing import Any, Callable

from clankops.enums import EventSource
from clankops.events import EVENT_COLUMNS, event_from_row
from clankops.gitinspect import inspect_git
from clankops.githubinspect import github_repo_id, inspect_github
from clankops.store import Store
from clankops.timefmt import short_head

InspectLocal = Callable[[str], dict[str, Any]]
InspectGitHub = Callable[[str | None], dict[str, Any]]


def working_tree_kind(label: str | None) -> str | None:
    if not label:
        return None
    text = str(label).strip().lower()
    if text.startswith("clean"):
        return "clean"
    if text.startswith("dirty"):
        return "dirty"
    return text


def working_tree_label(git: dict[str, Any]) -> str | None:
    if git.get("dirty") is True:
        count = git.get("dirty_count")
        return f"dirty ({count} paths)" if count is not None else "dirty"
    if git.get("dirty") is False:
        return "clean"
    return None


def heads_match(left: str | None, right: str | None) -> bool | None:
    if not left or not right:
        return None
    a, b = left.strip().lower(), right.strip().lower()
    if a == b:
        return True
    n = min(len(a), len(b))
    if n < 7:
        return False
    return a[:n] == b[:n]


def _drift(
    kind: str,
    field: str,
    recorded: Any,
    observed: Any,
    source: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "field": field,
        "recorded": recorded,
        "observed": observed,
        "source": source,
    }


def _checkpoint_event(store: Store, checkpoint: dict[str, Any] | None) -> Any | None:
    if not checkpoint:
        return None
    event_id = checkpoint.get("event_id")
    if not event_id:
        return None
    row = store.conn.execute(
        f"SELECT {EVENT_COLUMNS} FROM events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return event_from_row(row) if row else None


def _github_repo_for_clank(store: Store, clank_id: str) -> str | None:
    detail = store.clank_detail(clank_id)
    for ref in detail.get("refs") or []:
        if ref["ref_kind"] in {"git_remote", "github_repo"}:
            ident = github_repo_id(ref["ref_value"])
            if ident:
                return ident
    return None


def reconcile_clank(
    store: Store,
    clank: str,
    *,
    include_github: bool = True,
    inspect_local: InspectLocal | None = None,
    inspect_remote: InspectGitHub | None = None,
) -> dict[str, Any]:
    """Live observation vs recorded claims. Does not mutate the ledger."""
    local_fn = inspect_local or (lambda path: inspect_git(path))
    remote_fn = inspect_remote or inspect_github
    detail = store.clank_detail(clank)
    mission = store.active_or_unfinished_mission(detail["clank_id"])
    checkpoint = None
    if mission:
        checkpoint = store.latest_checkpoint(detail["clank_id"], mission["mission_id"])
    event = _checkpoint_event(store, checkpoint)
    git_evidence = (event.payload or {}).get("git_evidence") if event else None
    if not isinstance(git_evidence, dict):
        git_evidence = None
    recorded = {
        "branch": checkpoint.get("branch") if checkpoint else None,
        "head": checkpoint.get("head") if checkpoint else None,
        "working_tree": checkpoint.get("working_tree") if checkpoint else None,
        "checkpoint_utc": checkpoint.get("recorded_utc") if checkpoint else None,
        "checkpoint_id": checkpoint.get("checkpoint_id") if checkpoint else None,
        "event_source": event.source if event else None,
        "git_evidence": git_evidence,
    }
    path = store.canonical_local_path(detail["clank_id"])
    observed_local: dict[str, Any] = {
        "source": EventSource.LOCAL_GIT,
        "path": path,
        "ok": False,
        "error": None,
        "branch": None,
        "head": None,
        "working_tree": None,
        "dirty": None,
        "ahead": None,
        "behind": None,
        "upstream": None,
    }
    if not path:
        observed_local["error"] = "no local_path"
    else:
        git = local_fn(path)
        err = git.get("git_error")
        if err:
            observed_local["error"] = err
        elif not git.get("is_git"):
            observed_local["error"] = "not a git checkout"
        else:
            observed_local.update(
                {
                    "ok": True,
                    "branch": git.get("current_branch"),
                    "head": git.get("head"),
                    "working_tree": working_tree_label(git),
                    "dirty": git.get("dirty"),
                    "ahead": git.get("ahead"),
                    "behind": git.get("behind"),
                    "upstream": git.get("upstream"),
                }
            )

    observed_github: dict[str, Any] | None = None
    repo = _github_repo_for_clank(store, detail["clank_id"])
    if include_github:
        observed_github = remote_fn(repo)

    drift: list[dict[str, Any]] = []
    evidence = git_evidence or {}
    if evidence.get("head") and recorded["head"]:
        if heads_match(evidence.get("head"), recorded["head"]) is False:
            drift.append(
                _drift(
                    "claim_vs_git_evidence",
                    "head",
                    recorded["head"],
                    evidence.get("head"),
                    EventSource.LOCAL_GIT,
                )
            )
    if evidence.get("branch") and recorded["branch"]:
        if str(evidence.get("branch")).strip() != str(recorded["branch"]).strip():
            drift.append(
                _drift(
                    "claim_vs_git_evidence",
                    "branch",
                    recorded["branch"],
                    evidence.get("branch"),
                    EventSource.LOCAL_GIT,
                )
            )

    if observed_local["ok"] and recorded["branch"]:
        live_branch = observed_local.get("branch")
        if live_branch and live_branch != recorded["branch"]:
            drift.append(
                _drift(
                    "mismatch",
                    "branch",
                    recorded["branch"],
                    live_branch,
                    EventSource.LOCAL_GIT,
                )
            )
    if observed_local["ok"] and recorded["head"]:
        match = heads_match(recorded["head"], observed_local.get("head"))
        if match is False:
            drift.append(
                _drift(
                    "mismatch",
                    "head",
                    recorded["head"],
                    observed_local.get("head"),
                    EventSource.LOCAL_GIT,
                )
            )
    if observed_local["ok"] and recorded["working_tree"]:
        rec_kind = working_tree_kind(recorded["working_tree"])
        live_kind = working_tree_kind(observed_local.get("working_tree"))
        if rec_kind and live_kind and rec_kind != live_kind:
            drift.append(
                _drift(
                    "mismatch",
                    "working_tree",
                    recorded["working_tree"],
                    observed_local.get("working_tree"),
                    EventSource.LOCAL_GIT,
                )
            )

    if observed_github and observed_github.get("ok"):
        live_head = observed_local.get("head") if observed_local["ok"] else None
        live_branch = observed_local.get("branch") if observed_local["ok"] else None
        default_branch = observed_github.get("default_branch")
        default_head = observed_github.get("default_branch_head")
        if (
            live_branch
            and default_branch
            and live_branch == default_branch
            and live_head
            and default_head
            and heads_match(live_head, default_head) is False
        ):
            drift.append(
                _drift(
                    "mismatch",
                    "default_branch_head",
                    live_head,
                    default_head,
                    EventSource.GITHUB,
                )
            )
        recorded_branch = recorded.get("branch")
        for pr in observed_github.get("open_prs") or []:
            if recorded_branch and pr.get("head_ref") == recorded_branch:
                if recorded["head"] and pr.get("head") and heads_match(recorded["head"], pr.get("head")) is False:
                    drift.append(
                        _drift(
                            "mismatch",
                            "pr_head",
                            recorded["head"],
                            pr.get("head"),
                            EventSource.GITHUB,
                        )
                    )
                if live_head and pr.get("head") and heads_match(live_head, pr.get("head")) is False:
                    drift.append(
                        _drift(
                            "mismatch",
                            "pr_head_vs_local",
                            live_head,
                            pr.get("head"),
                            EventSource.GITHUB,
                        )
                    )

    has_record = bool(recorded["branch"] or recorded["head"] or recorded["working_tree"])
    if drift:
        status = "drift"
    elif not has_record:
        status = "no-record"
    elif observed_local["ok"] or (observed_github and observed_github.get("ok")):
        status = "aligned"
    else:
        status = "unknown"

    return {
        "clank_id": detail["clank_id"],
        "slug": detail["slug"],
        "display_name": detail["display_name"],
        "mission_display": mission["display_id"] if mission else None,
        "status": status,
        "recorded": recorded,
        "observed_local": observed_local,
        "observed_github": observed_github,
        "drift": drift,
        "rewrote_history": False,
        "head_short_recorded": short_head(recorded.get("head")),
        "head_short_local": short_head(observed_local.get("head")),
    }


def reconcile_fleet(
    store: Store,
    *,
    include_github: bool = False,
    inspect_local: InspectLocal | None = None,
    inspect_remote: InspectGitHub | None = None,
) -> dict[str, Any]:
    rows = [
        reconcile_clank(
            store,
            clank["slug"],
            include_github=include_github,
            inspect_local=inspect_local,
            inspect_remote=inspect_remote,
        )
        for clank in store.list_clanks()
    ]
    counts = {"aligned": 0, "drift": 0, "no-record": 0, "unknown": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "rewrote_history": False,
        "include_github": include_github,
        "counts": counts,
        "rows": rows,
    }
