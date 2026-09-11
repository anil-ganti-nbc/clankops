"""Compare live Git/GitHub observations to recorded checkpoint claims.

Read-only. Never appends events, never updates projections, never fetches
into a product checkout, never closes Sessions, never rewrites history.

`aligned` is corroboration, not observer availability. A source with
`ok=True` is not evidence by itself.
"""

from __future__ import annotations

from typing import Any, Callable

from clankops.enums import EventSource
from clankops.errors import ValidationError
from clankops.events import EVENT_COLUMNS, event_from_row
from clankops.gitinspect import inspect_git
from clankops.githubinspect import github_repo_id, inspect_commit_status, inspect_github
from clankops.store import Store
from clankops.timefmt import short_head

InspectLocal = Callable[[str], dict[str, Any]]
InspectGitHub = Callable[[str | None], dict[str, Any]]

RECORDED_FIELDS = ("branch", "head", "working_tree")
COMPARISON_STATUSES = ("corroborated", "contradicted", "unobservable", "not_recorded")
RECONCILE_STATUSES = ("drift", "aligned", "partial", "no-record", "unknown")


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


def _is_detached(branch: str | None) -> bool:
    return not branch or str(branch).strip() in {"HEAD", "detached"}


def _truthy_canonical(value: Any) -> bool:
    return value in {True, 1, "1"}


def _unique_github_ids(refs: list[dict[str, Any]], *, kind: str | None, canonical: bool | None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if kind is not None and ref.get("ref_kind") != kind:
            continue
        if canonical is True and not _truthy_canonical(ref.get("is_canonical")):
            continue
        if canonical is False and _truthy_canonical(ref.get("is_canonical")):
            continue
        if ref.get("ref_kind") not in {"git_remote", "github_repo"}:
            continue
        ident = github_repo_id(ref.get("ref_value"))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        found.append(ident)
    return sorted(found)


def github_repo_choice(refs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Pick a GitHub repo without guessing among conflicting identities.

    Order is independent of insertion/query order:

    1. unique canonical ``github_repo``
    2. unique canonical GitHub ``git_remote``
    3. unique GitHub identity among remaining refs
    4. otherwise ambiguity or none — do not guess
    """
    rows = list(refs or [])
    empty = {
        "repo": None,
        "rule": None,
        "candidates": [],
        "ambiguous": False,
        "error": None,
    }
    canonical_repos = _unique_github_ids(rows, kind="github_repo", canonical=True)
    if len(canonical_repos) == 1:
        return {**empty, "repo": canonical_repos[0], "rule": "canonical_github_repo", "candidates": canonical_repos}
    if len(canonical_repos) > 1:
        return {
            **empty,
            "candidates": canonical_repos,
            "ambiguous": True,
            "error": "ambiguous canonical github_repo refs: " + ", ".join(canonical_repos),
        }

    canonical_remotes = _unique_github_ids(rows, kind="git_remote", canonical=True)
    if len(canonical_remotes) == 1:
        return {
            **empty,
            "repo": canonical_remotes[0],
            "rule": "canonical_git_remote",
            "candidates": canonical_remotes,
        }
    if len(canonical_remotes) > 1:
        return {
            **empty,
            "candidates": canonical_remotes,
            "ambiguous": True,
            "error": "ambiguous canonical git_remote refs: " + ", ".join(canonical_remotes),
        }

    remaining = _unique_github_ids(rows, kind=None, canonical=None)
    if len(remaining) == 1:
        return {**empty, "repo": remaining[0], "rule": "unique_github_ref", "candidates": remaining}
    if len(remaining) > 1:
        return {
            **empty,
            "candidates": remaining,
            "ambiguous": True,
            "error": "ambiguous GitHub refs: " + ", ".join(remaining),
        }
    return {**empty, "error": "no GitHub repo"}


def github_repo_for_clank(store: Store, clank_id: str) -> dict[str, Any]:
    detail = store.clank_detail(clank_id)
    return github_repo_choice(detail.get("refs") or [])


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


def _comparison(status: str, *, source: str | None = None, recorded: Any = None, observed: Any = None) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "recorded": recorded,
        "observed": observed,
    }


def _reduce_checks(recorded: Any, checks: list[tuple[str, Any, bool | None]]) -> dict[str, Any]:
    """Independent checks: True corroborates, False contradicts, None is skipped."""
    if recorded is None or recorded == "":
        return _comparison("not_recorded")
    corroborated: tuple[str, Any] | None = None
    contradicted: tuple[str, Any] | None = None
    for source, observed, match in checks:
        if match is True and corroborated is None:
            corroborated = (source, observed)
        elif match is False and contradicted is None:
            contradicted = (source, observed)
    if contradicted:
        return _comparison(
            "contradicted",
            source=contradicted[0],
            recorded=recorded,
            observed=contradicted[1],
        )
    if corroborated:
        return _comparison(
            "corroborated",
            source=corroborated[0],
            recorded=recorded,
            observed=corroborated[1],
        )
    return _comparison("unobservable", recorded=recorded)


def _status_from_comparisons(comparisons: dict[str, dict[str, Any]]) -> str:
    relevant = [comparisons[field] for field in RECORDED_FIELDS if comparisons[field]["status"] != "not_recorded"]
    if not relevant:
        return "no-record"
    statuses = {item["status"] for item in relevant}
    if "contradicted" in statuses:
        return "drift"
    if statuses == {"corroborated"}:
        return "aligned"
    if "corroborated" in statuses:
        return "partial"
    return "unknown"


def _drift_rows(comparisons: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for field, item in comparisons.items():
        if item["status"] != "contradicted":
            continue
        rows.append(
            {
                "kind": "mismatch",
                "field": field,
                "recorded": item.get("recorded"),
                "observed": item.get("observed"),
                "source": item.get("source"),
            }
        )
    return rows


def _matching_prs(observed_github: dict[str, Any] | None, recorded_branch: str | None) -> list[dict[str, Any]]:
    if not observed_github or not recorded_branch:
        return []
    return [
        pr
        for pr in (observed_github.get("open_prs") or [])
        if pr.get("head_ref") and str(pr.get("head_ref")) == str(recorded_branch)
    ]


def _mission_for_reconcile(store: Store, detail: dict[str, Any], mission: str | None) -> dict[str, Any] | None:
    """Default: brief helper. Explicit mission: that Mission's checkpoint only."""
    if not mission:
        return store.active_or_unfinished_mission(detail["clank_id"])
    row = store.resolve_mission(mission)
    if row["clank_id"] != detail["clank_id"]:
        owner = store.clank_detail(row["clank_id"])
        raise ValidationError(
            f"mission {row['display_id']} belongs to "
            f"{owner.get('slug') or row['clank_id']}, not {detail.get('slug')}"
        )
    return row


def reconcile_clank(
    store: Store,
    clank: str,
    *,
    include_github: bool = True,
    include_ci: bool = True,
    inspect_local: InspectLocal | None = None,
    inspect_remote: InspectGitHub | None = None,
    mission: str | None = None,
) -> dict[str, Any]:
    """Live observation vs recorded claims. Does not mutate the ledger.

    ``clankctl reconcile`` leaves ``mission`` and ``include_ci`` at defaults.
    Capture may pass an explicit Mission and skip CI inspection.
    """
    local_fn = inspect_local or (lambda path: inspect_git(path))
    remote_fn = inspect_remote or inspect_github
    detail = store.clank_detail(clank)
    mission_row = _mission_for_reconcile(store, detail, mission)
    checkpoint = None
    if mission_row:
        checkpoint = store.latest_checkpoint(detail["clank_id"], mission_row["mission_id"])
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
        "detached": False,
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
            branch = git.get("current_branch")
            observed_local.update(
                {
                    "ok": True,
                    "branch": branch,
                    "head": git.get("head"),
                    "working_tree": working_tree_label(git),
                    "dirty": git.get("dirty"),
                    "ahead": git.get("ahead"),
                    "behind": git.get("behind"),
                    "upstream": git.get("upstream"),
                    "detached": _is_detached(branch),
                }
            )

    choice = github_repo_for_clank(store, detail["clank_id"])
    observed_github: dict[str, Any] | None = None
    if include_github:
        if choice.get("repo"):
            observed_github = remote_fn(choice["repo"])
            observed_github = {
                **(observed_github or {}),
                "selection_rule": choice.get("rule"),
                "candidates": choice.get("candidates") or [],
            }
        else:
            observed_github = {
                "source": EventSource.GITHUB,
                "ok": False,
                "error": choice.get("error") or "no GitHub repo",
                "repo": None,
                "default_branch": None,
                "default_branch_head": None,
                "open_prs": [],
                "ambiguous": bool(choice.get("ambiguous")),
                "candidates": choice.get("candidates") or [],
                "selection_rule": choice.get("rule"),
            }
        sha = (
            observed_local.get("head")
            or recorded.get("head")
            or (observed_github or {}).get("default_branch_head")
        )
        if include_ci and observed_github is not None and "checks" not in observed_github:
            if inspect_remote is None and choice.get("repo"):
                observed_github["checks"] = inspect_commit_status(choice["repo"], sha)
            else:
                observed_github["checks"] = {
                    "source": EventSource.GITHUB,
                    "ok": False,
                    "error": "not requested",
                    "sha": sha,
                    "state": None,
                    "runs": [],
                }

    github_ok = bool(observed_github and observed_github.get("ok"))
    default_branch = observed_github.get("default_branch") if github_ok else None
    default_head = observed_github.get("default_branch_head") if github_ok else None
    prs = _matching_prs(observed_github if github_ok else None, recorded.get("branch"))
    recorded_is_default = bool(
        recorded.get("branch") and default_branch and str(recorded["branch"]) == str(default_branch)
    )

    branch_checks: list[tuple[str, Any, bool | None]] = []
    if observed_local["ok"] and not observed_local["detached"] and observed_local.get("branch"):
        branch_checks.append(
            (
                EventSource.LOCAL_GIT,
                observed_local["branch"],
                str(observed_local["branch"]) == str(recorded.get("branch")),
            )
        )
    if recorded_is_default and default_branch:
        branch_checks.append((EventSource.GITHUB, default_branch, True))
    if prs:
        branch_checks.append((EventSource.GITHUB, prs[0].get("head_ref"), True))

    head_checks: list[tuple[str, Any, bool | None]] = []
    if observed_local["ok"] and observed_local.get("head"):
        head_checks.append(
            (
                EventSource.LOCAL_GIT,
                observed_local["head"],
                heads_match(recorded.get("head"), observed_local.get("head")),
            )
        )
    if recorded_is_default and default_head:
        head_checks.append(
            (
                EventSource.GITHUB,
                default_head,
                heads_match(recorded.get("head"), default_head),
            )
        )
    if prs and prs[0].get("head"):
        head_checks.append(
            (
                EventSource.GITHUB,
                prs[0].get("head"),
                heads_match(recorded.get("head"), prs[0].get("head")),
            )
        )

    tree_checks: list[tuple[str, Any, bool | None]] = []
    rec_kind = working_tree_kind(recorded.get("working_tree"))
    live_kind = working_tree_kind(observed_local.get("working_tree"))
    if observed_local["ok"] and rec_kind and live_kind:
        tree_checks.append(
            (
                EventSource.LOCAL_GIT,
                observed_local.get("working_tree"),
                rec_kind == live_kind,
            )
        )

    comparisons = {
        "branch": _reduce_checks(recorded.get("branch"), branch_checks),
        "head": _reduce_checks(recorded.get("head"), head_checks),
        "working_tree": _reduce_checks(recorded.get("working_tree"), tree_checks),
    }
    status = _status_from_comparisons(comparisons)
    drift = _drift_rows(comparisons)
    return {
        "clank_id": detail["clank_id"],
        "slug": detail["slug"],
        "display_name": detail["display_name"],
        "mission_display": mission_row["display_id"] if mission_row else None,
        "status": status,
        "comparisons": comparisons,
        "recorded": recorded,
        "observed_local": observed_local,
        "observed_github": observed_github,
        "github_selection": choice,
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
    counts = {key: 0 for key in RECONCILE_STATUSES}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "rewrote_history": False,
        "include_github": include_github,
        "counts": counts,
        "rows": rows,
    }
