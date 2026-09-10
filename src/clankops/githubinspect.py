"""Read-only GitHub observation. Never mutates remotes, PRs, or history."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from clankops.enums import EventSource

GH_TIMEOUT_SEC = 45
_GITHUB_REPO = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", re.I)
_OWNER_REPO = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?$", re.I)


def github_repo_id(url: str | None) -> str | None:
    if not url:
        return None
    text = url.strip()
    match = _GITHUB_REPO.search(text)
    if match:
        return f"{match.group('owner')}/{match.group('repo')}"
    if "\\" in text or ":" in text or text.startswith("."):
        return None
    match = _OWNER_REPO.match(text)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def run_gh(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=GH_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "gh executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "gh timed out"
    except OSError as exc:
        return 1, "", str(exc)


def inspect_github(repo: str | None) -> dict[str, Any]:
    """Observe open PRs and default-branch HEAD. GET-only `gh` calls."""
    result: dict[str, Any] = {
        "source": EventSource.GITHUB,
        "ok": False,
        "error": None,
        "repo": repo,
        "default_branch": None,
        "default_branch_head": None,
        "open_prs": [],
    }
    if not repo:
        result["error"] = "no GitHub repo"
        return result
    code, out, err = run_gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "30",
            "--json",
            "number,title,headRefName,url,isDraft,state,headRefOid",
        ]
    )
    if code != 0:
        result["error"] = err or out or f"gh pr list failed ({code})"
        return result
    try:
        raw_prs = json.loads(out or "[]")
    except json.JSONDecodeError:
        result["error"] = "gh pr list returned invalid JSON"
        return result
    prs = []
    for row in raw_prs:
        prs.append(
            {
                "number": row.get("number"),
                "title": row.get("title"),
                "head_ref": row.get("headRefName"),
                "head": row.get("headRefOid"),
                "url": row.get("url"),
                "is_draft": row.get("isDraft"),
                "state": row.get("state"),
            }
        )
    result["open_prs"] = prs

    code, out, err = run_gh(["api", f"repos/{repo}"])
    if code != 0:
        result["error"] = err or out or f"gh api repos/{repo} failed ({code})"
        result["ok"] = True  # PRs still usable
        return result
    try:
        meta = json.loads(out or "{}")
    except json.JSONDecodeError:
        result["error"] = "gh api repos returned invalid JSON"
        result["ok"] = True
        return result
    default_branch = meta.get("default_branch")
    result["default_branch"] = default_branch
    if default_branch:
        code, out, err = run_gh(["api", f"repos/{repo}/commits/{default_branch}"])
        if code == 0:
            try:
                commit = json.loads(out or "{}")
                result["default_branch_head"] = commit.get("sha")
            except json.JSONDecodeError:
                result["error"] = "gh api commit returned invalid JSON"
        else:
            result["error"] = err or out or f"gh api commit failed ({code})"
    result["ok"] = True
    return result


def _gh_json(args: list[str]) -> tuple[bool, Any, str | None]:
    code, out, err = run_gh(args)
    if code != 0:
        return False, None, err or out or f"gh {' '.join(args)} failed ({code})"
    try:
        return True, json.loads(out or "{}"), None
    except json.JSONDecodeError:
        return False, None, f"gh {' '.join(args)} returned invalid JSON"


def inspect_commit_status(repo: str | None, sha: str | None) -> dict[str, Any]:
    """Observe GitHub check-runs and status contexts for a SHA. GET-only."""
    result: dict[str, Any] = {
        "source": EventSource.GITHUB,
        "ok": False,
        "error": None,
        "repo": repo,
        "sha": sha,
        "state": None,
        "runs": [],
        "contexts": [],
        "checks_observed": False,
        "statuses_observed": False,
        "combined_state": None,
        "combined_total": None,
    }
    if not repo:
        result["error"] = "no GitHub repo"
        return result
    if not sha:
        result["error"] = "no commit SHA"
        return result

    checks_ok, payload, checks_err = _gh_json(["api", f"repos/{repo}/commits/{sha}/check-runs"])
    runs = []
    if checks_ok and isinstance(payload, dict):
        for row in payload.get("check_runs") or []:
            runs.append(
                {
                    "name": row.get("name"),
                    "status": row.get("status"),
                    "conclusion": row.get("conclusion"),
                    "html_url": row.get("html_url"),
                }
            )
        result["checks_observed"] = True
    result["runs"] = runs

    status_ok, combined, status_err = _gh_json(["api", f"repos/{repo}/commits/{sha}/status"])
    contexts = []
    combined_state = None
    combined_total = None
    if status_ok and isinstance(combined, dict):
        combined_state = combined.get("state")
        combined_total = combined.get("total_count")
        for row in combined.get("statuses") or []:
            contexts.append(
                {
                    "context": row.get("context"),
                    "state": row.get("state"),
                    "target_url": row.get("target_url"),
                    "description": row.get("description"),
                }
            )
        result["statuses_observed"] = True
    result["contexts"] = contexts
    result["combined_state"] = combined_state
    result["combined_total"] = combined_total

    errors = [item for item in (checks_err, status_err) if item]
    result["error"] = "; ".join(errors) if errors else None
    result["ok"] = bool(result["checks_observed"] or result["statuses_observed"])
    if not result["ok"]:
        return result
    result["state"] = rollup_ci_state(
        runs,
        contexts=contexts,
        checks_observed=result["checks_observed"],
        statuses_observed=result["statuses_observed"],
    )
    return result


_FAILED = {"failure", "cancelled", "timed_out", "action_required", "error"}
_NEUTRAL = {"skipped", "neutral", "stale"}


def _check_run_outcome(run: dict[str, Any]) -> str:
    """Incomplete check-runs are pending, never success."""
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if conclusion in _FAILED:
        return "failure"
    if status in {"queued", "in_progress", "pending"} or status != "completed":
        return "pending"
    if conclusion == "success":
        return "success"
    if conclusion in _NEUTRAL:
        return "neutral"
    return "unknown"


def _context_outcome(ctx: dict[str, Any]) -> str:
    state = str(ctx.get("state") or "").lower()
    if state in _FAILED:
        return "failure"
    if state == "pending":
        return "pending"
    if state == "success":
        return "success"
    return "unknown"


def rollup_ci_state(
    runs: list[dict[str, Any]] | None = None,
    *,
    contexts: list[dict[str, Any]] | None = None,
    checks_observed: bool = True,
    statuses_observed: bool = True,
) -> str:
    """Unified check-run + status-context roll-up.

    Failure beats pending beats unknown beats success. Empty observed
    check-runs and contexts are ``none``, never success. Combined GitHub
    ``pending``/``success`` with zero contexts is not evidence. If either
    observer is unavailable, empty evidence is ``unknown``, not ``none``.
    Success requires every check-run to be completed.
    """
    runs = list(runs or [])
    contexts = list(contexts or [])
    outcomes = [_check_run_outcome(run) for run in runs]
    outcomes.extend(_context_outcome(ctx) for ctx in contexts)
    if "failure" in outcomes:
        return "failure"
    if "pending" in outcomes:
        return "pending"
    if "unknown" in outcomes:
        return "unknown"
    if any(item == "success" for item in outcomes) and all(
        item in {"success", "neutral"} for item in outcomes
    ):
        return "success"
    if outcomes:
        return "unknown"
    if checks_observed and statuses_observed:
        return "none"
    return "unknown"
