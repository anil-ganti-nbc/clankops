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


def inspect_commit_status(repo: str | None, sha: str | None) -> dict[str, Any]:
    """Observe GitHub check-runs and combined status for a SHA. GET-only."""
    result: dict[str, Any] = {
        "source": EventSource.GITHUB,
        "ok": False,
        "error": None,
        "repo": repo,
        "sha": sha,
        "state": None,
        "runs": [],
    }
    if not repo:
        result["error"] = "no GitHub repo"
        return result
    if not sha:
        result["error"] = "no commit SHA"
        return result
    code, out, err = run_gh(["api", f"repos/{repo}/commits/{sha}/check-runs"])
    if code != 0:
        result["error"] = err or out or f"gh api check-runs failed ({code})"
        return result
    try:
        payload = json.loads(out or "{}")
    except json.JSONDecodeError:
        result["error"] = "gh api check-runs returned invalid JSON"
        return result
    runs = []
    for row in payload.get("check_runs") or []:
        runs.append(
            {
                "name": row.get("name"),
                "status": row.get("status"),
                "conclusion": row.get("conclusion"),
                "html_url": row.get("html_url"),
            }
        )
    result["runs"] = runs
    combined_state = None
    combined_total = None
    code, out, err = run_gh(["api", f"repos/{repo}/commits/{sha}/status"])
    if code == 0:
        try:
            combined = json.loads(out or "{}")
            combined_state = combined.get("state")
            combined_total = combined.get("total_count")
        except json.JSONDecodeError:
            result["error"] = "gh api commit status returned invalid JSON"
    else:
        result["error"] = err or out or f"gh api commit status failed ({code})"
    result["combined_state"] = combined_state
    result["combined_total"] = combined_total
    result["state"] = rollup_ci_state(runs, combined_state, combined_total)
    result["ok"] = True
    return result


def rollup_ci_state(
    runs: list[dict[str, Any]],
    combined_state: str | None = None,
    combined_total: int | None = None,
) -> str:
    """Empty checks/statuses are none, never success. Failure beats pending beats success.

    GitHub combined status for a SHA with zero contexts is often pending or even
    success. That is not CI evidence.
    """
    conclusions = [str(run.get("conclusion") or "").lower() for run in runs]
    statuses = [str(run.get("status") or "").lower() for run in runs]
    combined = (combined_state or "").lower()
    failed = {"failure", "cancelled", "timed_out", "action_required", "error"}
    if any(item in failed for item in conclusions):
        return "failure"
    if any(item in {"queued", "in_progress", "pending"} for item in statuses):
        return "pending"
    if runs and all(item == "success" for item in conclusions):
        return "success"
    if runs:
        return "unknown"
    try:
        total = int(combined_total) if combined_total is not None else 0
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        if combined in failed:
            return "failure"
        if combined == "pending":
            return "pending"
        if combined == "success":
            return "success"
        return "unknown"
    return "none"
