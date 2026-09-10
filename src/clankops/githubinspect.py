"""Read-only GitHub observation. Never mutates remotes, PRs, or history."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from clankops.enums import EventSource

GH_TIMEOUT_SEC = 45
_GITHUB_REPO = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", re.I)


def github_repo_id(url: str | None) -> str | None:
    if not url:
        return None
    match = _GITHUB_REPO.search(url.strip())
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
