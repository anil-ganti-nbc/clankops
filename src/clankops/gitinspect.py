"""Read-only Git inspection. Never mutates a repository."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from clankops.redact import redact_url

GIT_TIMEOUT_SEC = 30


def run_git(cwd: str | Path, args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"
    except OSError as exc:
        return 1, "", str(exc)


def inspect_git(path: str | Path) -> dict[str, Any]:
    """Inspect a directory for Git metadata without altering it.

    Dirty state is recorded, never cleaned. Ownership / safe.directory
    failures are captured as evidence rather than auto-fixed.
    """
    root = Path(path)
    result: dict[str, Any] = {
        "is_git": False,
        "git_error": None,
        "remotes": {},
        "canonical_remote": None,
        "default_branch": None,
        "current_branch": None,
        "head": None,
        "dirty": None,
        "dirty_count": 0,
        "dirty_sample": [],
        "latest_commit_timestamp": None,
        "latest_commit_subject": None,
        "latest_commit_author": None,
        "branches": [],
        "local_branches": [],
        "worktrees": None,
        "origin_head_ref": None,
    }
    git_dir = root / ".git"
    if not git_dir.exists():
        return result
    result["is_git"] = True
    code, out, err = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if code != 0:
        result["git_error"] = err or out or f"git rev-parse failed ({code})"
        return result

    code, out, err = run_git(root, ["remote", "-v"])
    remotes: dict[str, list[dict[str, str]]] = {}
    if code == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                name, url = parts[0], redact_url(parts[1]) or parts[1]
                kind = parts[2] if len(parts) > 2 else ""
                remotes.setdefault(name, []).append({"url": url, "kind": kind})
    result["remotes"] = remotes
    if "origin" in remotes:
        result["canonical_remote"] = remotes["origin"][0]["url"]
    elif remotes:
        first = next(iter(remotes.values()))
        result["canonical_remote"] = first[0]["url"]

    code, out, _ = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if code == 0:
        result["current_branch"] = out
    code, out, _ = run_git(root, ["rev-parse", "HEAD"])
    if code == 0:
        result["head"] = out
    code, out, _ = run_git(root, ["log", "-1", "--format=%cI%x09%s%x09%an"])
    if code == 0 and out:
        bits = out.split("\t", 2)
        result["latest_commit_timestamp"] = bits[0] or None
        result["latest_commit_subject"] = bits[1] if len(bits) > 1 else None
        result["latest_commit_author"] = bits[2] if len(bits) > 2 else None
    code, out, _ = run_git(root, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if code == 0 and out:
        result["origin_head_ref"] = out
        result["default_branch"] = out.rsplit("/", 1)[-1]
    code, out, _ = run_git(root, ["branch", "-a", "--format=%(refname:short)"])
    if code == 0:
        result["branches"] = [b for b in out.splitlines() if b]
    code, out, _ = run_git(
        root,
        ["branch", "--format=%(refname:short)|%(committerdate:iso-strict)|%(upstream:short)"],
    )
    if code == 0:
        local = []
        for line in out.splitlines():
            name, _, rest = line.partition("|")
            date, _, upstream = rest.partition("|")
            local.append(
                {
                    "name": name,
                    "committerdate": date or None,
                    "upstream": upstream or None,
                }
            )
        result["local_branches"] = local
    code, out, _ = run_git(root, ["status", "--porcelain=v1", "-b"])
    if code == 0:
        lines = out.splitlines()
        result["status_branch_line"] = lines[0] if lines else ""
        dirty_lines = [ln for ln in lines[1:] if ln.strip()]
        result["dirty"] = bool(dirty_lines)
        result["dirty_count"] = len(dirty_lines)
        result["dirty_sample"] = dirty_lines[:20]
    else:
        result["dirty"] = None
    code, out, _ = run_git(root, ["worktree", "list", "--porcelain"])
    if code == 0:
        result["worktrees"] = out or None
    code, out, _ = run_git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if code == 0 and out:
        result["upstream"] = out
        acode, aout, _ = run_git(root, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        if acode == 0 and aout:
            left, _, right = aout.strip().partition("\t")
            if not right:
                left, _, right = aout.strip().partition(" ")
            try:
                result["behind"] = int(left)
                result["ahead"] = int(right)
            except ValueError:
                result["behind"] = None
                result["ahead"] = None
    if result["default_branch"] is None and result["current_branch"] not in {None, "HEAD"}:
        result["default_branch"] = result["current_branch"]
    return result
